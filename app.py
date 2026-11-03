"""Amazon Image Studio — simple Image API powered team workflow.
Run: uvicorn app:app --host 127.0.0.1 --port 8000
"""
import io, json, os, sqlite3, uuid, secrets, hmac, re, csv, hashlib, logging, ipaddress
from datetime import datetime, timezone, timedelta
from pathlib import Path
from contextlib import asynccontextmanager
from typing import Optional
from PIL import Image, ImageDraw, ImageOps
from fastapi import FastAPI, Form, File, UploadFile, HTTPException, Request, Cookie, Depends, Header
from fastapi.responses import FileResponse, Response, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from studioflow.settings import Settings
from studioflow.database import connect, initialize
from studioflow.jobs import RenderQueue
from studioflow.submissions import validate_key, fingerprint, replay, remember
from studioflow.provider import MODEL_CONFIG, PRESETS, DEFAULT_MODEL, REVISION_MODEL, validate_settings, render_live
from studioflow import aplus, usage

BASE = Path(__file__).resolve().parent
settings = Settings.load(BASE)
DATA = settings.data_dir
UPLOADS = DATA / 'uploads'
OUTPUTS = DATA / 'outputs'
BRAND_LOGOS = DATA / 'brand_logos'
STATIC = BASE / 'static'
DB = DATA / 'studio.sqlite3'
MODE, KEY, MAX_WORKERS = settings.mode, settings.api_key, settings.max_workers
TEAM, ADMIN_USERS = settings.team, settings.admins
MAX_IMAGES = 250
MAX_PENDING_IMAGES = 1000
MAX_IMAGE_BYTES = 18 * 1024 * 1024
MAX_BATCH_BYTES = 100 * 1024 * 1024
logger = logging.getLogger(__name__)

CSV_COLUMNS = [
    'ASIN', 'Item Code', 'SKU', 'Colour', 'Size', 'Variation',
    'Title', 'Item Highlight', 'Bullet Point 1', 'Bullet Point 2',
    'Bullet Point 3', 'Bullet Point 4', 'Bullet Point 5'
]

def db():
    return connect(DB)


def init_db():
    initialize(DB)


@asynccontextmanager
async def lifespan(application):
    for directory in (DATA, UPLOADS, OUTPUTS, BRAND_LOGOS):
        directory.mkdir(parents=True, exist_ok=True)
    await run_in_threadpool(init_db)
    queue = RenderQueue(db, generate_one, MAX_WORKERS)
    application.state.render_queue = queue
    await run_in_threadpool(queue.start)
    try:
        yield
    finally:
        await run_in_threadpool(queue.stop)


app = FastAPI(title='Amazon Image Studio', lifespan=lifespan)
app.mount('/static', StaticFiles(directory=STATIC), name='static')


@app.middleware('http')
async def restrict_anonymous_demo(request: Request, call_next):
    if settings.anonymous_demo:
        peer = request.client.host if request.client else ''
        try:
            local_peer = ipaddress.ip_address(peer).is_loopback
        except ValueError:
            local_peer = False
        local_host = request.url.hostname in ('localhost', '127.0.0.1', '::1')
        if not local_peer or not local_host:
            return JSONResponse(status_code=403, content={'detail': 'Anonymous demos are available only on localhost. Configure team accounts for network access.'})
    return await call_next(request)


@app.get('/api/health')
def health():
    with db() as c:
        c.execute('SELECT 1').fetchone()
    return {'status': 'ok'}


def now():return datetime.now(timezone.utc).isoformat(timespec='seconds')

def get_session_user(token: str) -> Optional[str]:
    if not token:return None
    with db() as c:
        row=c.execute("SELECT user, expires_at FROM sessions WHERE token=?",(token,)).fetchone()
        if not row:return None
        if row['user'] not in TEAM or row['expires_at'] < now():
            c.execute("DELETE FROM sessions WHERE token=?",(token,))
            return None
        return row['user']

def create_session(user: str, max_age_days: int = 7) -> str:
    token = secrets.token_urlsafe(32)
    created = now()
    expires = (datetime.now(timezone.utc) + timedelta(days=max_age_days)).isoformat(timespec='seconds')
    with db() as c:
        c.execute("INSERT INTO sessions (token, user, created_at, expires_at) VALUES (?, ?, ?, ?)",
                  (token, user, created, expires))
    return token

def delete_session(token: str):
    if token:
        with db() as c:
            c.execute("DELETE FROM sessions WHERE token=?", (token,))

def current_user(studio_session: Optional[str]=Cookie(default=None)):
    if MODE=='demo' and not TEAM:return 'Demo user'
    if not studio_session:raise HTTPException(401,'Sign in required')
    user = get_session_user(studio_session)
    if not user:raise HTTPException(401,'Session expired. Sign in again.')
    return user

def owned(r,user):
    if not r:raise HTTPException(404,'Not found')
    return dict(r)

@app.get('/')
def home():return FileResponse(STATIC/'index.html')

class LoginBody(BaseModel):
    username:str
    password:str

@app.post('/api/login')
def login(body:LoginBody,response:Response):
    if not TEAM:
        if MODE=='demo':return {'user':'Demo user','mode':MODE}
        raise HTTPException(500,'Team users are not configured')
    accepted=False
    for name,password in TEAM.items():
        if hmac.compare_digest(name.encode(),body.username.encode()) and hmac.compare_digest(password.encode(),body.password.encode()):
            accepted=True
            break
    if not accepted:raise HTTPException(401,'Incorrect username or password')
    token = create_session(body.username, max_age_days=7)
    secure_cookie = settings.secure_cookie
    response.set_cookie('studio_session', token, httponly=True, secure=secure_cookie, samesite='lax', max_age=7*24*3600)
    return {'user':body.username,'mode':MODE}

@app.post('/api/logout')
def logout(response:Response,studio_session:Optional[str]=Cookie(default=None)):
    if studio_session:
        delete_session(studio_session)
    response.delete_cookie('studio_session')
    return {'ok':True}

@app.get('/api/config')
def config(studio_session:Optional[str]=Cookie(default=None)):
    person='Demo user' if MODE=='demo' and not TEAM else None
    if studio_session:
        person = get_session_user(studio_session) or person
    with db() as c:
        cat_count = c.execute("SELECT COUNT(*) n FROM catalog_products").fetchone()['n']
    return {
        'mode':MODE,
        'user':person,
        'login_required':bool(TEAM),
        'max_workers':MAX_WORKERS,
        'max_products':50,
        'max_scenes':12,
        'max_images':MAX_IMAGES,
        'models':MODEL_CONFIG,
        'features':{'aplus':settings.enable_aplus,'usage':settings.enable_usage},
        'catalog_count':cat_count
    }

@app.get('/api/stats')
def stats(user:str=Depends(current_user)):
    with db() as c:
        count=c.execute("SELECT COUNT(*) n FROM renders WHERE status='completed'").fetchone()['n']
        approved=c.execute("SELECT COUNT(*) n FROM renders WHERE approved=1").fetchone()['n']
        pending=c.execute("SELECT COUNT(*) n FROM renders WHERE status IN ('queued','running')").fetchone()['n']
        total=c.execute('SELECT COUNT(*) n FROM jobs').fetchone()['n']
        cat_total=c.execute('SELECT COUNT(*) n FROM catalog_products').fetchone()['n']
    return {'generated':count,'approved':approved,'pending':pending,'jobs':total,'catalog':cat_total}

BASE_RULES='''Use the uploaded photograph as the exact source of truth for the product. Retain the real product's silhouette, colour, texture, materials, count, proportions, labels, and decoration. Do not invent attachments or modify the product itself. Photorealistic commercial photography, realistic perspective and scale, sharp product details, clean lighting. No children. Only adult women if a human model is requested.'''

def canonical_sku(sku):
    if not isinstance(sku,str):
        raise HTTPException(400,'Product identifier must be text')
    key=sku.strip().upper()
    if not 1<=len(key)<=60 or any(c in key for c in '/\\\\?&#%') or any(ord(c)<32 for c in key):
        raise HTTPException(400,'SKU must be 1-60 characters and not contain URL special characters')
    return key

def is_admin(user):
    return (settings.anonymous_demo and user=='Demo user') or user in ADMIN_USERS

def format_catalog_facts(p: dict) -> str:
    lines = []
    if p.get('title'):
        lines.append(f"Title: {p['title']}")
    ids = []
    if p.get('asin'): ids.append(f"ASIN: {p['asin']}")
    if p.get('item_code'): ids.append(f"Item Code: {p['item_code']}")
    if p.get('sku'): ids.append(f"SKU: {p['sku']}")
    if ids:
        lines.append(" | ".join(ids))
    attrs = []
    if p.get('colour'): attrs.append(f"Colour: {p['colour']}")
    if p.get('size'): attrs.append(f"Size: {p['size']}")
    if p.get('variation'): attrs.append(f"Variation: {p['variation']}")
    if attrs:
        lines.append("Attributes: " + ", ".join(attrs))
    if p.get('item_highlight'):
        lines.append(f"Key Highlight: {p['item_highlight']}")
    bullets = [p.get(f'bullet_{i}', '').strip() for i in range(1, 6) if p.get(f'bullet_{i}', '').strip()]
    if bullets:
        lines.append("Key Features:")
        for b in bullets:
            lines.append(f"- {b}")
    return "\n".join(lines)

def find_catalog_product(c, identifier: str) -> Optional[dict]:
    if not identifier: return None
    key = canonical_sku(identifier)
    # A SKU is authoritative. Aliases can be shared by catalog variations, so
    # never select an arbitrary row when an ASIN or item code is ambiguous.
    rows = c.execute('SELECT * FROM catalog_products WHERE sku=? COLLATE NOCASE LIMIT 2',(key,)).fetchall()
    if not rows:
        rows = c.execute('''SELECT * FROM catalog_products
            WHERE asin=? COLLATE NOCASE OR item_code=? COLLATE NOCASE LIMIT 2''',(key,key)).fetchall()
    if len(rows)>1:
        raise HTTPException(409,'Product identifier matches multiple catalog items. Use the exact SKU.')
    return dict(rows[0]) if rows else None


def resolve_product_sku(c, identifier: str) -> str:
    key=canonical_sku(identifier)
    product=find_catalog_product(c,key)
    return canonical_sku(product['sku']) if product else key

def memory_snapshot(sku, extra_rule='', connection=None):
    from contextlib import nullcontext
    with (nullcontext(connection) if connection is not None else db()) as c:
        key=resolve_product_sku(c,sku)
        r=c.execute("SELECT value FROM memory_settings WHERE key='master_rules'").fetchone()
        product=c.execute('SELECT facts,brand_id FROM product_memory WHERE sku=?',(key,)).fetchone()
        cat=find_catalog_product(c, key)
        rules=[x['instruction'] for x in c.execute('SELECT instruction FROM product_rules WHERE sku=? ORDER BY created_at,id',(key,))]
        brand_id = (product['brand_id'] if product and product['brand_id'] else (cat['brand_id'] if cat else ''))
        br=c.execute('SELECT * FROM brands WHERE id=?',(brand_id,)).fetchone() if brand_id else None
    
    if extra_rule.strip():rules.append(extra_rule.strip())
    branding={k:br[k] for k in ('id','name','primary_color','secondary_color','accent_color','heading_font','body_font','visual_style','brand_rules')} if br else None
    
    combined_facts = []
    if cat and cat.get('facts_summary'):
        combined_facts.append(cat['facts_summary'])
    if product and product['facts'] and product['facts'].strip():
        if not cat or product['facts'].strip() != cat.get('facts_summary', '').strip():
            combined_facts.append(product['facts'].strip())
            
    facts_str = "\n\n".join(combined_facts)
    
    return json.dumps({
        'company': r['value'] if r else BASE_RULES,
        'facts': facts_str,
        'rules': rules,
        'sku': key,
        'branding': branding,
        'catalog': cat
    }, ensure_ascii=False)

def audit(c,user,event,sku,content):
    c.execute('INSERT INTO memory_audit VALUES (?,?,?,?,?,?)',
              (uuid.uuid4().hex,user,event,sku,content[:5000],now()))


app.include_router(aplus.build_router(db, current_user, is_admin, BRAND_LOGOS, lambda: settings.enable_aplus, audit))
app.include_router(usage.build_router(db, current_user, is_admin, lambda: settings.enable_usage, audit))

# ----------------- Product Catalog Endpoints ----------------- #

@app.get('/api/catalog/template')
def download_catalog_template():
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=CSV_COLUMNS)
    writer.writeheader()
    writer.writerow({
        'ASIN': 'B08SAMPLE01',
        'Item Code': 'ITM-101',
        'SKU': 'SKU-BLUE-L',
        'Colour': 'Royal Blue',
        'Size': 'Large',
        'Variation': 'Blue Variation',
        'Title': 'Insulated Stainless Steel Water Bottle 750ml',
        'Item Highlight': 'Double-wall vacuum insulation keeps drinks cold for 24h',
        'Bullet Point 1': 'Food-grade 18/8 stainless steel construction',
        'Bullet Point 2': 'Leak-proof lid with silicone seal and carry loop',
        'Bullet Point 3': 'Wide mouth for ice cubes and easy cleaning',
        'Bullet Point 4': 'Sweat-free powder-coated exterior finish',
        'Bullet Point 5': 'BPA-free and non-toxic eco-friendly design'
    })
    writer.writerow({
        'ASIN': 'B08SAMPLE02',
        'Item Code': 'ITM-102',
        'SKU': 'SKU-RED-M',
        'Colour': 'Crimson Red',
        'Size': 'Medium',
        'Variation': 'Red Variation',
        'Title': 'Ergonomic Memory Foam Lumbar Support Cushion',
        'Item Highlight': 'High-density premium memory foam core with breathable mesh cover',
        'Bullet Point 1': 'Ergonomically contoured for spine alignment and posture',
        'Bullet Point 2': 'Removable and machine-washable 3D mesh cover',
        'Bullet Point 3': 'Adjustable dual straps fit office chairs, cars and recliners',
        'Bullet Point 4': 'Relieves lower back pain, sciatica and pressure points',
        'Bullet Point 5': 'Non-slip rubber bottom keeps cushion in place all day'
    })
    csv_bytes = output.getvalue().encode('utf-8-sig')
    return Response(
        content=csv_bytes,
        media_type='text/csv',
        headers={'Content-Disposition': 'attachment; filename="studioflow_product_template.csv"'}
    )

@app.post('/api/catalog/import')
def import_catalog(file: UploadFile = File(...), user: str = Depends(current_user)):
    raw = file.file.read(10 * 1024 * 1024 + 1)
    if len(raw) > 10 * 1024 * 1024:
        raise HTTPException(400, 'Catalog file must be under 10 MB')
    try:
        text = raw.decode('utf-8-sig')
    except UnicodeDecodeError:
        try:
            text = raw.decode('latin-1')
        except Exception:
            raise HTTPException(400, 'Unable to decode CSV file. Please use UTF-8 or CSV encoding.')
            
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise HTTPException(400, 'CSV file has no header row')
        
    field_map = {}
    for h in reader.fieldnames:
        clean_h = re.sub(r'[^a-zA-Z0-9]', '', h.lower())
        if clean_h == 'asin': field_map[h] = 'asin'
        elif clean_h in ('itemcode', 'itemcod', 'code', 'item_code'): field_map[h] = 'item_code'
        elif clean_h in ('sku', 'itemsku'): field_map[h] = 'sku'
        elif clean_h in ('colour', 'color'): field_map[h] = 'colour'
        elif clean_h == 'size': field_map[h] = 'size'
        elif clean_h in ('variation', 'variant'): field_map[h] = 'variation'
        elif clean_h in ('title', 'producttitle', 'name', 'productname'): field_map[h] = 'title'
        elif clean_h in ('itemhighlight', 'highlight', 'highlights', 'keyhighlight'): field_map[h] = 'item_highlight'
        elif clean_h in ('bulletpoint1', 'bullet1', 'bp1', 'bulletpoint_1'): field_map[h] = 'bullet_1'
        elif clean_h in ('bulletpoint2', 'bullet2', 'bp2', 'bulletpoint_2'): field_map[h] = 'bullet_2'
        elif clean_h in ('bulletpoint3', 'bullet3', 'bp3', 'bulletpoint_3'): field_map[h] = 'bullet_3'
        elif clean_h in ('bulletpoint4', 'bullet4', 'bp4', 'bulletpoint_4'): field_map[h] = 'bullet_4'
        elif clean_h in ('bulletpoint5', 'bullet5', 'bp5', 'bulletpoint_5'): field_map[h] = 'bullet_5'

    if not {'sku','asin','item_code'}.intersection(field_map.values()):
        raise HTTPException(400,'CSV needs an SKU, ASIN or Item Code column')

    imported = 0
    updated = 0
    with db() as c:
        # Serialize the lookup/upsert sequence, including databases created
        # before SKU uniqueness was enforced.
        c.execute('BEGIN IMMEDIATE')
        for row_number,row in enumerate(reader,start=2):
            item = {}
            for original_h, val in row.items():
                if original_h in field_map:
                    item[field_map[original_h]] = str(val or '').strip()
                    
            sku = (item.get('sku') or item.get('asin') or item.get('item_code') or '').strip().upper()
            if not sku:
                continue
            try:
                sku=canonical_sku(sku)
                asin=canonical_sku(item['asin']) if item.get('asin') else ''
                item_code=canonical_sku(item['item_code']) if item.get('item_code') else ''
            except HTTPException as ex:
                raise HTTPException(400,f'CSV row {row_number}: {ex.detail}') from ex
            colour = item.get('colour', '').strip()
            size = item.get('size', '').strip()
            variation = item.get('variation', '').strip()
            title = item.get('title', '').strip()
            highlight = item.get('item_highlight', '').strip()
            b1 = item.get('bullet_1', '').strip()
            b2 = item.get('bullet_2', '').strip()
            b3 = item.get('bullet_3', '').strip()
            b4 = item.get('bullet_4', '').strip()
            b5 = item.get('bullet_5', '').strip()
            
            p_dict = {
                'sku': sku, 'asin': asin, 'item_code': item_code,
                'colour': colour, 'size': size, 'variation': variation,
                'title': title, 'item_highlight': highlight,
                'bullet_1': b1, 'bullet_2': b2, 'bullet_3': b3, 'bullet_4': b4, 'bullet_5': b5
            }
            facts = format_catalog_facts(p_dict)
            
            existing_rows=c.execute('SELECT id,facts_summary FROM catalog_products WHERE sku=? COLLATE NOCASE LIMIT 2',(sku,)).fetchall()
            if len(existing_rows)>1:
                raise HTTPException(409,f'CSV row {row_number}: duplicate catalog SKU {sku}. Resolve duplicates before importing.')
            existing=existing_rows[0] if existing_rows else None
            if existing:
                cid = existing['id']
                # Older imports copied their generated summary into the manual
                # facts field. Remove only an unchanged copy with no later
                # manual-edit audit, preserving actual human edits.
                legacy=c.execute('SELECT facts,updated_at FROM product_memory WHERE sku=?',(sku,)).fetchone()
                if legacy and legacy['facts'] and legacy['facts']==existing['facts_summary']:
                    edited=c.execute("""SELECT 1 FROM memory_audit WHERE event='sku_facts_updated'
                        AND sku=? AND created_at>=? LIMIT 1""",(sku,legacy['updated_at'] or '')).fetchone()
                    if not edited:
                        c.execute("UPDATE product_memory SET facts='' WHERE sku=?",(sku,))
                c.execute("""
                    UPDATE catalog_products SET 
                    asin=?, item_code=?, colour=?, size=?, variation=?,
                    title=?, item_highlight=?, bullet_1=?, bullet_2=?, bullet_3=?, bullet_4=?, bullet_5=?,
                    facts_summary=?, updated_by=?, updated_at=?
                    WHERE id=?
                """, (asin, item_code, colour, size, variation, title, highlight, b1, b2, b3, b4, b5, facts, user, now(), cid))
                updated += 1
            else:
                cid = uuid.uuid4().hex
                c.execute("""
                    INSERT INTO catalog_products (
                        id, sku, asin, item_code, colour, size, variation,
                        title, item_highlight, bullet_1, bullet_2, bullet_3, bullet_4, bullet_5,
                        facts_summary, updated_by, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (cid, sku, asin, item_code, colour, size, variation, title, highlight, b1, b2, b3, b4, b5, facts, user, now()))
                imported += 1
                
            # Catalog-derived facts stay in catalog_products. This row stores
            # only manually maintained facts, corrections, and branding.
            c.execute('''INSERT OR IGNORE INTO product_memory(sku,facts,updated_by,updated_at)
                VALUES(?,?,?,?)''',(sku,'',user,now()))
            
        total = c.execute("SELECT COUNT(*) n FROM catalog_products").fetchone()['n']
        audit(c, user, 'catalog_csv_imported', '', f'Imported: {imported}, Updated: {updated}')
        
    return {'ok': True, 'imported': imported, 'updated': updated, 'total': total}

@app.get('/api/catalog')
def get_catalog(q: str = '', user: str = Depends(current_user)):
    term = f"%{q.strip().upper()}%"
    with db() as c:
        if q.strip():
            rows = c.execute("""
                SELECT * FROM catalog_products 
                WHERE UPPER(sku) LIKE ? OR UPPER(asin) LIKE ? OR UPPER(item_code) LIKE ? OR UPPER(title) LIKE ?
                ORDER BY updated_at DESC LIMIT 200
            """, (term, term, term, term)).fetchall()
        else:
            rows = c.execute("SELECT * FROM catalog_products ORDER BY updated_at DESC LIMIT 200").fetchall()
        total = c.execute("SELECT COUNT(*) n FROM catalog_products").fetchone()['n']
    return {'products': [dict(r) for r in rows], 'total': total}

@app.delete('/api/catalog/{id}')
def delete_catalog_product(id: str, user: str = Depends(current_user)):
    if not is_admin(user):
        raise HTTPException(403, 'Only workspace owner can delete catalog items')
    with db() as c:
        c.execute("DELETE FROM catalog_products WHERE id=?", (id,))
    return {'ok': True}

class MatchFilesBody(BaseModel):
    filenames: list[str]=Field(max_length=50)

@app.post('/api/catalog/match-files')
def match_files(body: MatchFilesBody, user: str = Depends(current_user)):
    with db() as c:
        products = [dict(r) for r in c.execute('SELECT sku, asin, item_code, colour, size, variation, title FROM catalog_products').fetchall()]
    
    def norm(s):
        return re.sub(r'[^a-zA-Z0-9]', '', str(s or '').lower())
        
    matches = {}
    for filename in body.filenames:
        base = re.sub(r'\.[^.]+$', '', str(filename)).strip()
        norm_base = norm(base)
        best_p = None
        best_score = 0
        method = ''
        ambiguous = False
        
        for p in products:
            n_sku = norm(p['sku'])
            n_asin = norm(p['asin'])
            n_code = norm(p['item_code'])
            
            score = 0
            m_type = ''
            
            if n_sku and n_sku == norm_base:
                score = 100; m_type = 'Exact SKU'
            elif n_asin and n_asin == norm_base:
                score = 95; m_type = 'Exact ASIN'
            elif n_code and n_code == norm_base:
                score = 90; m_type = 'Exact Item Code'
            elif n_sku and len(n_sku) >= 3 and n_sku in norm_base:
                score = 80; m_type = 'SKU in filename'
            elif n_asin and len(n_asin) >= 4 and n_asin in norm_base:
                score = 75; m_type = 'ASIN in filename'
            elif n_code and len(n_code) >= 3 and n_code in norm_base:
                score = 70; m_type = 'Item Code in filename'
                
            if score > best_score:
                best_score = score
                best_p = p
                method = m_type
                ambiguous = False
            elif score and score == best_score and best_p and p['sku']!=best_p['sku']:
                ambiguous = True
                
        if best_p and best_score >= 70 and not ambiguous:
            matches[filename] = {
                'matched': True,
                'score': best_score,
                'method': method,
                'sku': best_p['sku'],
                'asin': best_p['asin'],
                'item_code': best_p['item_code'],
                'title': best_p['title'],
                'colour': best_p['colour'],
                'variation': best_p['variation']
            }
        else:
            matches[filename] = {'matched': False, 'ambiguous': ambiguous}
            
    return {'matches': matches}

# ----------------- Memory & Brand Management ----------------- #

class CompanyMemoryUpdate(BaseModel):
    instructions:str=Field(min_length=15,max_length=7000)

@app.get('/api/memory/company')
def get_company_memory(user:str=Depends(current_user)):
    with db() as c:
        r=c.execute("SELECT * FROM memory_settings WHERE key='master_rules'").fetchone()
    return {'instructions':r['value'] if r else BASE_RULES,
            'updated_by':r['updated_by'] if r else 'StudioFlow default',
            'updated_at':r['updated_at'] if r else '', 'can_edit':is_admin(user)}

@app.put('/api/memory/company')
def put_company_memory(body:CompanyMemoryUpdate,user:str=Depends(current_user)):
    if not is_admin(user):raise HTTPException(403,'Only the workspace owner can edit company instructions')
    with db() as c:
        c.execute("INSERT INTO memory_settings(key,value,updated_by,updated_at) VALUES('master_rules',?,?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_by=excluded.updated_by,updated_at=excluded.updated_at",
                  (body.instructions.strip(),user,now()))
        audit(c,user,'company_rules_updated','',body.instructions)
    return {'ok':True}

HEX_RE=re.compile(r'^#[0-9a-fA-F]{6}$')

class BrandData(BaseModel):
    name:str=Field(min_length=2,max_length=100)
    primary_color:str='#233A54'
    secondary_color:str='#F2EEE8'
    accent_color:str='#B98E5C'
    heading_font:str=Field(default='Cinzel',max_length=90)
    body_font:str=Field(default='Roboto',max_length=90)
    visual_style:str=Field(default='',max_length=1600)
    brand_rules:str=Field(default='',max_length=4000)

def brand_payload(row):
    r=dict(row)
    r['logo_url']=f'/api/memory/brands/{r["id"]}/logo' if r.get('logo_file') else ''
    r.pop('logo_file',None)
    return r

def check_brand(body):
    name=body.name.strip()
    if len(name)<2:raise HTTPException(400,'Brand name must have at least 2 characters')
    for key in ('primary_color','secondary_color','accent_color'):
        if not HEX_RE.fullmatch(getattr(body,key)):
            raise HTTPException(400,key+' must be a 6-digit hex colour (#RRGGBB)')
    return name

@app.get('/api/memory/brands')
def list_brands(user:str=Depends(current_user)):
    with db() as c:rows=c.execute('SELECT * FROM brands ORDER BY name COLLATE NOCASE').fetchall()
    return [brand_payload(r) for r in rows]

@app.post('/api/memory/brands')
def create_brand(body:BrandData,user:str=Depends(current_user)):
    if not is_admin(user):raise HTTPException(403,'Only an owner can create brand profiles')
    name=check_brand(body);bid=uuid.uuid4().hex
    with db() as c:
        try:
            c.execute('''INSERT INTO brands(id,name,primary_color,secondary_color,accent_color,heading_font,body_font,visual_style,brand_rules,updated_by,updated_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?)''',(bid,name,body.primary_color.upper(),body.secondary_color.upper(),body.accent_color.upper(),body.heading_font.strip(),body.body_font.strip(),body.visual_style.strip(),body.brand_rules.strip(),user,now()))
        except sqlite3.IntegrityError:raise HTTPException(409,'A brand with this name already exists')
        audit(c,user,'brand_created','',name)
    return {'ok':True,'id':bid}

@app.put('/api/memory/brands/{bid}')
def update_brand(bid:str,body:BrandData,user:str=Depends(current_user)):
    if not is_admin(user):raise HTTPException(403,'Only an owner can edit brand profiles')
    name=check_brand(body)
    with db() as c:
        if not c.execute('SELECT 1 FROM brands WHERE id=?',(bid,)).fetchone():raise HTTPException(404,'Brand not found')
        try:
            c.execute('''UPDATE brands SET name=?,primary_color=?,secondary_color=?,accent_color=?,heading_font=?,body_font=?,visual_style=?,brand_rules=?,updated_by=?,updated_at=? WHERE id=?''',
            (name,body.primary_color.upper(),body.secondary_color.upper(),body.accent_color.upper(),body.heading_font.strip(),body.body_font.strip(),body.visual_style.strip(),body.brand_rules.strip(),user,now(),bid))
        except sqlite3.IntegrityError:raise HTTPException(409,'A brand with this name already exists')
        audit(c,user,'brand_updated','',name)
    return {'ok':True,'id':bid}

@app.post('/api/memory/brands/{bid}/logo')
def upload_brand_logo(bid:str,file:UploadFile=File(...),user:str=Depends(current_user)):
    if not is_admin(user):raise HTTPException(403,'Only an owner can upload brand logos')
    with db() as c:
        previous=c.execute('SELECT logo_file FROM brands WHERE id=?',(bid,)).fetchone()
    if not previous:raise HTTPException(404,'Brand not found')
    raw=file.file.read(5*1024*1024+1)
    if len(raw)>5*1024*1024:raise HTTPException(400,'Logo must be under 5MB')
    try:
        with Image.open(io.BytesIO(raw)) as src:
            if src.format not in ('PNG','JPEG','WEBP') or src.width<16 or src.height<16 or src.width*src.height>16_000_000:
                raise ValueError('Invalid logo size or format')
            logo=ImageOps.exif_transpose(src).convert('RGBA')
            logo.thumbnail((1800,1800),Image.Resampling.LANCZOS)
            filename=f'{uuid.uuid4().hex}.png'
            logo.save(BRAND_LOGOS/filename,'PNG')
    except Exception:raise HTTPException(400,'Upload a valid PNG, JPG or WEBP logo')
    with db() as c:
        c.execute('UPDATE brands SET logo_file=?,updated_by=?,updated_at=? WHERE id=?',(filename,user,now(),bid))
        audit(c,user,'brand_logo_uploaded','',bid)
    old=previous['logo_file']
    if old and old!=filename and (BRAND_LOGOS/old).exists():
        (BRAND_LOGOS/old).unlink()
    return {'ok':True,'logo_url':f'/api/memory/brands/{bid}/logo'}

@app.get('/api/memory/brands/{bid}/logo')
def get_brand_logo(bid:str,user:str=Depends(current_user)):
    with db() as c: r=c.execute('SELECT logo_file FROM brands WHERE id=?',(bid,)).fetchone()
    if not r or not r['logo_file']:raise HTTPException(404,'Brand logo not found')
    filename=r['logo_file']
    if not re.fullmatch(r'[0-9a-f]{32}\.png',filename) or not (BRAND_LOGOS/filename).is_file():
        raise HTTPException(404,'Brand logo not found')
    return FileResponse(BRAND_LOGOS/filename,media_type='image/png')

class AssignBrand(BaseModel):
    brand_id:Optional[str]=None

@app.put('/api/memory/products/{sku}/brand')
def assign_brand(sku:str,body:AssignBrand,user:str=Depends(current_user)):
    if not is_admin(user):raise HTTPException(403,'Only an owner can assign product branding')
    bid=(body.brand_id or '').strip()
    with db() as c:
        key=resolve_product_sku(c,sku)
        if bid and not c.execute('SELECT 1 FROM brands WHERE id=?',(bid,)).fetchone():raise HTTPException(404,'Brand not found')
        c.execute('INSERT OR IGNORE INTO product_memory(sku,facts,updated_by,updated_at) VALUES(?,?,?,?)',(key,'',user,now()))
        c.execute('UPDATE product_memory SET brand_id=?,updated_by=?,updated_at=? WHERE sku=?',(bid,user,now(),key))
        audit(c,user,'sku_brand_assigned',key,bid or '(none)')
    return {'ok':True,'sku':key,'brand_id':bid}

@app.get('/api/memory/products')
def list_product_memory(q:str='',user:str=Depends(current_user)):
    with db() as c:
        rows=[dict(r) for r in c.execute('''SELECT p.sku,p.facts,p.updated_at,p.updated_by,
        (SELECT COUNT(*) FROM product_rules r WHERE r.sku=p.sku) rule_count,
        COALESCE((SELECT name FROM brands b WHERE b.id=p.brand_id),'') brand_name
        FROM product_memory p WHERE p.sku LIKE ? ORDER BY p.updated_at DESC LIMIT 100''',('%'+q.strip().upper()[:60]+'%',))]
    return rows

@app.get('/api/memory/products/{sku}')
def get_product_memory(sku:str,user:str=Depends(current_user)):
    with db() as c:
        key=resolve_product_sku(c,sku)
        r=c.execute('SELECT * FROM product_memory WHERE sku=?',(key,)).fetchone()
        cat=find_catalog_product(c, key)
        rules=[dict(x) for x in c.execute('SELECT * FROM product_rules WHERE sku=? ORDER BY created_at,id',(key,))]
    for item in rules:item['can_delete']=is_admin(user) or item['created_by']==user
    
    brand_id = r['brand_id'] if r and r['brand_id'] else (cat['brand_id'] if cat else '')
    facts = r['facts'] if r else ''
    
    return {'sku':key,'facts':facts, 'catalog_facts':cat['facts_summary'] if cat else '', 'brand_id':brand_id,
            'updated_by':r['updated_by'] if r else '', 'updated_at':r['updated_at'] if r else '',
            'can_manage_branding':is_admin(user), 'rules':rules, 'catalog':cat}

class ProductMemoryUpdate(BaseModel):
    facts:str=Field(max_length=6000)

@app.put('/api/memory/products/{sku}')
def put_product_memory(sku:str,body:ProductMemoryUpdate,user:str=Depends(current_user)):
    with db() as c:
        key=resolve_product_sku(c,sku)
        c.execute('''INSERT INTO product_memory(sku,facts,updated_by,updated_at) VALUES(?,?,?,?)
        ON CONFLICT(sku) DO UPDATE SET facts=excluded.facts,updated_by=excluded.updated_by,updated_at=excluded.updated_at''',
                  (key,body.facts.strip(),user,now()))
        audit(c,user,'sku_facts_updated',key,body.facts)
    return {'ok':True,'sku':key}

class ProductRuleUpdate(BaseModel):
    instruction:str=Field(min_length=5,max_length=2000)

@app.post('/api/memory/products/{sku}/rules')
def add_product_rule(sku:str,body:ProductRuleUpdate,user:str=Depends(current_user)):
    rid=uuid.uuid4().hex
    with db() as c:
        key=resolve_product_sku(c,sku)
        c.execute('INSERT OR IGNORE INTO product_memory(sku,facts,updated_by,updated_at) VALUES(?,?,?,?)',(key,'',user,now()))
        c.execute('INSERT INTO product_rules VALUES (?,?,?,?,?,?)',(rid,key,body.instruction.strip(),user,now(),''))
        audit(c,user,'sku_rule_added',key,body.instruction)
    return {'ok':True,'id':rid}

@app.delete('/api/memory/products/{sku}/rules/{rid}')
def delete_product_rule(sku:str,rid:str,user:str=Depends(current_user)):
    with db() as c:
        key=resolve_product_sku(c,sku)
        rule=c.execute('SELECT * FROM product_rules WHERE id=? AND sku=?',(rid,key)).fetchone()
        if not rule:raise HTTPException(404,'Rule not found')
        if not (is_admin(user) or rule['created_by']==user):raise HTTPException(403,'Only owner or rule author can delete')
        c.execute('DELETE FROM product_rules WHERE id=?',(rid,))
        audit(c,user,'sku_rule_deleted',key,rule['instruction'])
    return {'ok':True}

@app.get('/api/renders/{rid}/history')
def render_history(rid:str,user:str=Depends(current_user)):
    chain=[];visited=set()
    with db() as c:
        curr=c.execute('SELECT * FROM renders WHERE id=?',(rid,)).fetchone()
        if not curr:raise HTTPException(404,'Image not found')
        while curr and curr['id'] not in visited and len(chain)<50:
            visited.add(curr['id'])
            item=dict(curr)
            chain.append({'id':item['id'],'sku':item['sku'],'user':item['user'],'created_at':item['created_at'],
                          'prompt':item['prompt'],'status':item['status'],'memory_snapshot':json.loads(item['memory_snapshot'] or '{}'),
                          'url':f'/api/media/{item["file"]}' if item['file'] else None})
            curr=c.execute('SELECT * FROM renders WHERE id=?',(item['parent_id'],)).fetchone() if item['parent_id'] else None
    chain.reverse()
    return chain

TASK_RULES={
    'aplus':'Create an Amazon Premium A+ banner background. Preserve the exact product and leave space for later copy. Do not render text, numbers, logos, divider lines, children or invented product features.',
    'lifestyle':'Create a photorealistic lifestyle product image. Product should be clearly visible in an appropriate setting. No advertising text or extra logos.',
    'main':'Create a compliant-style Amazon MAIN product shot: pure white RGB(255,255,255) background, product only, centered, fills most of frame, no props, no labels added, no text. Preserve original packaging and genuine product marks. No people.',
    'infographic':'Create a clean product-focused graphic with generous negative space for overlay text. Do NOT generate words, numbers or labels; these will be added as crisp editable text after generation. Product remains accurate and clearly visible.',
}

def make_prompt(kind,scene,memory_json=''):
    memory=json.loads(memory_json or '{}')
    company=memory.get('company') or BASE_RULES
    facts=memory.get('facts') or ''
    rules=memory.get('rules') or []
    sku=memory.get('sku') or ''
    branding=memory.get('branding') or None
    catalog=memory.get('catalog') or None
    
    if kind=='aplus' and memory.get('aplus'):
        brand_info=aplus.prompt(memory['aplus'])
    elif kind=='main':
        brand_info='Brand profile is not applied to MAIN image visual styling. Do not add new text, graphic logo, advertising, borders, or branding overlays. Genuine marks already on the product must remain unchanged.'
    elif branding:
        brand_info=('BRAND IDENTITY — apply to the background/composition only, not the physical product:\n'
                    f'Brand: {branding["name"]}\nPrimary colour: {branding["primary_color"]}; '
                    f'Secondary colour: {branding["secondary_color"]}; Accent colour: {branding["accent_color"]}\n'
                    f'Heading font (for downstream text layout): {branding["heading_font"]}; '
                    f'Body font (for downstream text layout): {branding["body_font"]}\n'
                    f'Visual style: {branding["visual_style"] or "not specified"}\n'
                    f'Brand-specific guardrails: {branding["brand_rules"] or "none"}\n'
                    'Logo reference is saved in StudioFlow separately; do not invent, mimic, draw, or add a logo. '
                    'Do not render marketing text into the generated image; approved typography and logo should be applied in post-production.')
    else:
        brand_info='No brand profile assigned.'
        
    catalog_context = ''
    if catalog:
        catalog_lines = []
        if catalog.get('title'): catalog_lines.append(f"Product Title: {catalog['title']}")
        if catalog.get('colour'): catalog_lines.append(f"Colour: {catalog['colour']}")
        if catalog.get('size'): catalog_lines.append(f"Size: {catalog['size']}")
        if catalog.get('variation'): catalog_lines.append(f"Variation: {catalog['variation']}")
        if catalog.get('item_highlight'): catalog_lines.append(f"Key Highlight: {catalog['item_highlight']}")
        bullets = [catalog.get(f'bullet_{i}') for i in range(1, 6) if catalog.get(f'bullet_{i}')]
        if bullets:
            catalog_lines.append("Features to visually reflect or stage for:\n" + "\n".join(f"- {b}" for b in bullets))
        if catalog_lines:
            catalog_context = "STRUCTURED PRODUCT SPECIFICATIONS:\n" + "\n".join(catalog_lines) + "\n\n"
        
    return ('COMPANY INSTRUCTIONS:\n'+company+'\n\n'+
            'PRODUCT IDENTIFIER: '+sku+'\n\n'+
            catalog_context+
            'VERIFIED PRODUCT FACTS (do not invent missing facts):\n'+(facts or 'No extra facts saved')+'\n\n'+
            'PRODUCT-SPECIFIC CORRECTIONS, MUST PRESERVE:\n'+('\n'.join('- '+r for r in rules) if rules else 'No extra corrections saved')+'\n\n'+
            'BRANDING:\n'+brand_info+'\n\n'+
            'IMAGE TYPE:\n'+TASK_RULES[kind]+'\n\nSCENE / CURRENT INSTRUCTIONS:\n'+scene)

def save_image_to_png(raw:bytes,path:Path):
    if len(raw)>18*1024*1024:raise HTTPException(400,'Images must be under 18 MB each')
    try:
        with Image.open(io.BytesIO(raw)) as img:
            if img.format not in ('PNG','JPEG','WEBP'):raise HTTPException(400,'Upload a JPG, PNG or WEBP image')
            if img.width<120 or img.height<120:raise HTTPException(400,'Image is too small (minimum 120px per side)')
            if img.width*img.height>45_000_000:raise HTTPException(400,'Image dimensions are too large')
            ImageOps.exif_transpose(img).convert('RGB').save(path,'PNG')
    except HTTPException:raise
    except Exception:raise HTTPException(400,'Please upload a valid JPG, PNG or WEBP image')

def render_demo(source:Path,target:Path,kind:str,scene:str,canvas:str):
    _,out_size=PRESETS[canvas]
    w,h=out_size
    background={'lifestyle':'#ebe7df','main':'#ffffff','infographic':'#e8eff5','aplus':'#e9e6de'}[kind]
    image=Image.new('RGB',(w,h),background)
    with Image.open(source) as original:
        original=original.convert('RGBA')
        tw=int(w*(0.60 if kind=='lifestyle' else 0.78));th=int(h*0.70)
        original.thumbnail((tw,th),Image.Resampling.LANCZOS)
        x=(w-original.width)//2;y=(h-original.height)//2
        image.paste(original,(x,y),original)
    d=ImageDraw.Draw(image)
    d.rounded_rectangle((20,20,w-20,max(86,int(h*.10))),radius=15,fill='#142b3e')
    d.text((42,36),'DEMO PREVIEW — NO AI USED',fill='white')
    d.text((42,h-52),'Connect OPENAI_API_KEY + APP_MODE=live for real image editing',fill='#39536c')
    image.save(target,'JPEG',quality=90)

def finish_job(c, job_id):
    c.execute("UPDATE jobs SET status='finished' WHERE id=? AND NOT EXISTS "
              "(SELECT 1 FROM renders WHERE job_id=? AND status IN ('queued','running'))", (job_id, job_id))


def generate_one(r, owner):
    output = OUTPUTS / f'{r["id"]}.jpg'
    attempt = None

    def started():
        nonlocal attempt
        attempt = usage.begin_attempt(db, r)

    def received(response):
        usage.record_response(db, attempt, response)

    try:
        if MODE == 'demo':
            render_demo(UPLOADS / r['source'], output, r['kind'], r['prompt'], r['canvas'])
        else:
            inputs = [UPLOADS / r['source']]
            if r['original']:
                prior = OUTPUTS / r['original']
                if not prior.is_file():
                    raise RuntimeError('Previous image could not be found for revision')
                inputs = [prior, UPLOADS / r['source']]
            render_live(inputs, output, make_prompt(r['kind'], r['prompt'], r['memory_snapshot']),
                        r['model'], r['quality'], r['canvas'], KEY,
                        aplus=r['kind']=='aplus', on_request=started, on_response=received)
        usage.finish_attempt(db, attempt, True)
        with db() as c:
            c.execute("UPDATE renders SET status='completed',file=?,error='' WHERE id=? AND worker_id=? AND status='running'",
                      (output.name, r['id'], owner))
            finish_job(c, r['job_id'])
    except Exception as exc:
        usage.finish_attempt(db, attempt, False)
        logger.exception('Render %s failed', r['id'])
        # Store actionable categories, never provider responses containing secrets.
        error = str(exc).lower()
        message = 'Image generation failed. Check server logs before retrying.'
        if 'invalid_api_key' in error or 'incorrect api key' in error:
            message = 'Image provider authentication failed. Check the configured API key.'
        elif 'quota' in error:
            message = 'Image provider quota exceeded. Check workspace billing.'
        elif 'rate_limit' in error:
            message = 'Image provider rate limit reached. Try again later.'
        elif 'safety' in error or 'content_policy' in error:
            message = 'The image provider rejected this prompt. Refine the scene description.'
        with db() as c:
            c.execute("UPDATE renders SET status='failed',error=? WHERE id=? AND worker_id=? AND status='running'",
                      (message, r['id'], owner))
            finish_job(c, r['job_id'])


def validate_render_settings(model, quality, canvas):
    try:
        validate_settings(model, quality, canvas)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


def check_capacity(c, count):
    pending = c.execute("SELECT COUNT(*) FROM renders WHERE status IN ('queued','running')").fetchone()[0]
    if pending + count > MAX_PENDING_IMAGES:
        raise HTTPException(429, 'The production queue is full. Wait for existing images to finish.')


@app.post('/api/generate')
def generate(files: list[UploadFile] = File(...), skus: str = Form(...), kind: str = Form(...),
             scenes: str = Form(...), scene_sets: str = Form(''), model: str = Form(DEFAULT_MODEL),
             quality: str = Form('medium'), canvas: str = Form('square'),
             aplus_profile_id: str = Form(''),
             idempotency_key: Optional[str] = Header(default=None, alias='Idempotency-Key'),
             user: str = Depends(current_user)):
    validate_key(idempotency_key)
    if kind not in TASK_RULES:
        raise HTTPException(400, 'Unsupported image type')
    if kind == 'aplus':
        if not settings.enable_aplus:
            raise HTTPException(404, 'A+ Studio is disabled')
        if canvas not in ('banner', 'mobile'):
            raise HTTPException(400, 'A+ requires the desktop banner or mobile canvas')
        if not aplus_profile_id:
            raise HTTPException(400, 'Choose a saved A+ profile')
    elif aplus_profile_id:
        raise HTTPException(400, 'An A+ profile applies only to A+ generation')
    validate_render_settings(model, quality, canvas)
    if not 1 <= len(files) <= 50:
        raise HTTPException(400, 'Upload 1–50 product photos per job')
    try:
        sku_list = json.loads(skus)
        overrides = json.loads(scene_sets) if scene_sets else [''] * len(files)
    except (ValueError, TypeError):
        raise HTTPException(400, 'Invalid product identifiers or scene overrides')
    if not isinstance(sku_list, list) or len(sku_list) != len(files) or not all(isinstance(x, str) for x in sku_list):
        raise HTTPException(400, 'Provide one text SKU per uploaded photo')
    sku_list = [canonical_sku(sku or f'Product-{index + 1}') for index, sku in enumerate(sku_list)]
    defaults = [s.strip() for s in scenes.splitlines() if s.strip()]
    if not 1 <= len(defaults) <= 12:
        raise HTTPException(400, 'Enter 1–12 default scenes, one per line')
    if not isinstance(overrides, list) or len(overrides) != len(files):
        raise HTTPException(400, 'Scene overrides must match product count')
    per_product = []
    for override in overrides:
        if not isinstance(override, str):
            raise HTTPException(400, 'Invalid scene override')
        lines = [x.strip() for x in override.splitlines() if x.strip()] if override.strip() else defaults
        if not 1 <= len(lines) <= 12 or any(len(line) > 3500 for line in lines):
            raise HTTPException(400, 'Each product needs 1–12 scenes of at most 3500 characters')
        per_product.append(lines)
    count = sum(map(len, per_product))
    if count > MAX_IMAGES:
        raise HTTPException(400, f'A job can contain at most {MAX_IMAGES} images')
    raw_images = []
    total_bytes = 0
    for upload in files:
        raw = upload.file.read(MAX_IMAGE_BYTES + 1)
        total_bytes += len(raw)
        if len(raw) > MAX_IMAGE_BYTES or total_bytes > MAX_BATCH_BYTES:
            raise HTTPException(413, 'Photos must be under 18 MB each and 100 MB combined')
        raw_images.append(raw)
    request_data = {'skus': sku_list, 'kind': kind, 'scenes': per_product,
                    'model': model, 'quality': quality, 'canvas': canvas}
    if kind == 'aplus':
        request_data['aplus_profile_id'] = aplus_profile_id
    digest = fingerprint(request_data, raw_images)
    with db() as c:
        previous = replay(c, user, idempotency_key, 'generate', digest)
        if previous:
            return previous
    saved = []
    committed = False
    try:
        for raw in raw_images:
            filename = f'{uuid.uuid4().hex}.png'
            saved.append(filename)
            save_image_to_png(raw, UPLOADS / filename)
        job_id = uuid.uuid4().hex
        result = {'job_id': job_id, 'images_queued': count, 'mode': MODE}
        with db() as c:
            c.execute('BEGIN IMMEDIATE')
            previous = replay(c, user, idempotency_key, 'generate', digest)
            if previous:
                return previous
            check_capacity(c, count)
            profile = aplus.snapshot(c, aplus_profile_id) if kind == 'aplus' else None
            c.execute('INSERT INTO jobs VALUES(?,?,?,?,?,?)', (job_id, user, kind, now(), count, 'running'))
            for index, source in enumerate(saved):
                sku = resolve_product_sku(c, sku_list[index])
                snapshot = memory_snapshot(sku, connection=c)
                if profile:
                    snapshot = json.dumps({**json.loads(snapshot), 'aplus': profile}, ensure_ascii=False)
                for scene in per_product[index]:
                    if len(make_prompt(kind, scene, snapshot)) > 32000:
                        raise HTTPException(400, 'Combined product memory and scene exceed the 32000-character provider limit')
                    render_id = uuid.uuid4().hex
                    c.execute('INSERT INTO renders (id,job_id,user,sku,kind,source,original,prompt,model,quality,canvas,created_at,memory_snapshot) '
                              'VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',
                              (render_id, job_id, user, sku, kind, source, '', scene, model, quality, canvas, now(), snapshot))
                    if profile:
                        c.execute('UPDATE renders SET aplus_profile_id=? WHERE id=?', (aplus_profile_id, render_id))
            remember(c, user, idempotency_key, 'generate', digest, result, now())
        committed = True
        return result
    finally:
        if not committed:
            for filename in saved:
                (UPLOADS / filename).unlink(missing_ok=True)


class Revision(BaseModel):
    render_id: str
    instructions: str = Field(min_length=5, max_length=3500)
    model: str = REVISION_MODEL
    quality: str = 'high'
    remember_rule: bool = False


@app.post('/api/revise')
def revise(body: Revision, idempotency_key: Optional[str] = Header(default=None, alias='Idempotency-Key'),
           user: str = Depends(current_user)):
    validate_key(idempotency_key)
    if len(body.instructions.strip()) < 5:
        raise HTTPException(400, 'Explain the correction in at least five characters')
    digest = fingerprint(body.model_dump())
    with db() as c:
        c.execute('BEGIN IMMEDIATE')
        previous = replay(c, user, idempotency_key, 'revise', digest)
        if previous:
            return previous
        parent = owned(c.execute('SELECT * FROM renders WHERE id=?', (body.render_id,)).fetchone(), user)
        if parent['status'] != 'completed':
            raise HTTPException(400, 'Only completed images can be revised')
        if parent['kind'] == 'aplus' and not settings.enable_aplus:
            raise HTTPException(404, 'A+ Studio is disabled')
        validate_render_settings(body.model, body.quality, parent['canvas'])
        check_capacity(c, 1)
        sku = resolve_product_sku(c, parent['sku'])
        snapshot = memory_snapshot(sku, connection=c)
        if parent['kind'] == 'aplus':
            original_profile = json.loads(parent['memory_snapshot'] or '{}').get('aplus')
            if not original_profile:
                raise HTTPException(409, 'This A+ render has no saved profile snapshot. Create a new A+ job.')
            snapshot = json.dumps({**json.loads(snapshot), 'aplus': original_profile}, ensure_ascii=False)
        if len(make_prompt(parent['kind'], body.instructions, snapshot)) > 32000:
            raise HTTPException(400, 'Combined product memory and revision exceed the 32000-character provider limit')
        job_id, rid = uuid.uuid4().hex, uuid.uuid4().hex
        c.execute('INSERT INTO jobs VALUES(?,?,?,?,?,?)', (job_id, user, parent['kind'], now(), 1, 'running'))
        c.execute('INSERT INTO renders (id,job_id,user,sku,kind,source,original,prompt,model,quality,canvas,parent_id,created_at,memory_snapshot,pending_rule) '
                  'VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                  (rid, job_id, user, sku, parent['kind'], parent['source'], parent['file'], body.instructions.strip(),
                   body.model, body.quality, parent['canvas'], parent['id'], now(), snapshot,
                   body.instructions.strip() if body.remember_rule else ''))
        if parent['kind'] == 'aplus':
            c.execute('UPDATE renders SET aplus_profile_id=? WHERE id=?', (parent['aplus_profile_id'], rid))
        result = {'job_id': job_id, 'render_id': rid, 'mode': MODE, 'saved_rule': False, 'pending_rule': body.remember_rule}
        remember(c, user, idempotency_key, 'revise', digest, result, now())
    return result


@app.get('/api/renders')
def renders(user:str=Depends(current_user)):
    with db() as c:
        rows=[dict(x) for x in c.execute('SELECT * FROM renders ORDER BY created_at DESC, rowid DESC LIMIT 300')]
    for r in rows:
        r['url']=f'/api/media/{r["file"]}' if r['file'] else None
        r.pop('source',None);r.pop('original',None);r.pop('memory_snapshot',None)
    return rows

@app.get('/api/jobs')
def jobs(user:str=Depends(current_user)):
    with db() as c:
        rows=[dict(x) for x in c.execute('''SELECT j.*,SUM(CASE WHEN r.status='completed' THEN 1 ELSE 0 END) completed,
           SUM(CASE WHEN r.status='failed' THEN 1 ELSE 0 END) failed FROM jobs j
           LEFT JOIN renders r ON j.id=r.job_id GROUP BY j.id ORDER BY j.created_at DESC LIMIT 25''')]
    return rows

@app.get('/api/media/{filename}')
def media(filename:str,user:str=Depends(current_user)):
    if not re.fullmatch(r'[0-9a-f]{32}\.jpg', filename):
        raise HTTPException(404,'Image not found')
    with db() as c:
        exists=c.execute('SELECT id FROM renders WHERE file=?',(filename,)).fetchone()
    if not exists or not (OUTPUTS/filename).exists():raise HTTPException(404,'Image not found')
    return FileResponse(OUTPUTS/filename,media_type='image/jpeg',filename=filename)

class Approval(BaseModel):
    approved:bool

@app.post('/api/renders/{rid}/approve')
def approve(rid:str,body:Approval,user:str=Depends(current_user)):
    with db() as c:
        c.execute('BEGIN IMMEDIATE')
        row=c.execute('SELECT * FROM renders WHERE id=?',(rid,)).fetchone()
        if not row:raise HTTPException(404,'Image not found')
        if row['status']!='completed':raise HTTPException(400,'Image not ready')
        c.execute('UPDATE renders SET approved=? WHERE id=?',(int(body.approved),rid))
        if body.approved and row['pending_rule']:
            sku = resolve_product_sku(c, row['sku'])
            c.execute('INSERT OR IGNORE INTO product_memory(sku,facts,updated_by,updated_at) VALUES(?,?,?,?)', (sku, '', user, now()))
            c.execute('INSERT INTO product_rules VALUES(?,?,?,?,?,?)',
                      (uuid.uuid4().hex, sku, row['pending_rule'], user, now(), rid))
            c.execute("UPDATE renders SET pending_rule='' WHERE id=?", (rid,))
            audit(c, user, 'approved_revision_saved_as_rule', sku, row['pending_rule'])
    return {'approved':body.approved}
