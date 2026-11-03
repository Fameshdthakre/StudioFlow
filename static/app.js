let config={mode:'demo',user:'Demo user',login_required:false}, products=[],renderRecords=[],jobRecords=[],catalogProducts=[],kind='lifestyle',selected=null,visible='create',currentMemorySku='',currentMemory=null,brandProfiles=[],brandEditorNew=false;
let isSubmitting=false,isRevising=false,catalogRequest=0,memoryRequest=0,brandRequest=0,historyRequest=0,modalReturnFocus=null;
const pendingRequests={generate:null,revise:null},fileIds=new WeakMap();
let nextFileId=0;
const $=id=>document.getElementById(id);
const escapeHtml=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const SCENES={
 lifestyle:[
  'Styled naturally in a bright contemporary living room with realistic morning daylight, product clearly visible.',
  'Elegant warm-toned home interior with tasteful furnishings, a close realistic product composition.',
  'Detailed close-up showing the genuine product texture and construction in a relevant environment.',
  'Premium lifestyle setting with natural shadows, professional commercial photography and true-to-life scale.',
  'Seasonally appropriate display with uncluttered composition, accurate materials and natural depth of field.'
 ],
 main:['Pure white background, full product visible, centered, no props or added decoration.'],
 infographic:[
  'Product centered with clean negative space on the right for adding accurate text in post-processing.',
  'Product detail close-up with clean background and generous negative space for feature callouts.',
  'Product scale and size presentation, no rendered numbers or text; leave space for exact dimensions.'
 ]
};
const TIP={lifestyle:'Tip: Each line becomes a separate lifestyle setup. Describe settings relevant to this particular product.',main:'MAIN images must be checked against Amazon category policies. Use the genuine product on pure white, without extra props.',infographic:'For sharp Cinzel/Roboto headings, generate the scene without words and add exact typography later in a graphic editor.'};
function toast(message,bad=false){const el=document.createElement('div');el.className='toast'+(bad?' bad':'');el.textContent=message;$('toasts').append(el);setTimeout(()=>el.remove(),5500)}
async function api(path,options={}){const res=await fetch(path,{credentials:'same-origin',...options});let json;try{json=await res.json()}catch{json={detail:'Could not read server response'}}if(!res.ok){if(res.status===401 && config.login_required)$('login-overlay').classList.remove('hidden');throw new Error(typeof json.detail==='string'?json.detail:'Request failed')}return json}
function setView(name){
  visible=name;
  for(const view of ['create','catalog','gallery','jobs','memory','aplus','usage']){
    const vEl=$(`${view}-view`);if(vEl)vEl.classList.toggle('hidden',name!==view);
    const nEl=$(`nav-${view}`);if(nEl)nEl.classList.toggle('active',name===view);
  }
  if(name==='catalog')loadCatalog();
  if(name==='gallery')loadRenders();
  if(name==='jobs')loadJobs();
  if(name==='memory')loadMemoryHome();
  if(name==='aplus')window.StudioFlowAplus?.load();
  if(name==='usage')window.StudioFlowUsage?.load();
}
function sceneCount(){return $('scenes').value.split('\n').filter(x=>x.trim()).length}
function assignedCount(){return products.reduce((sum,p)=>sum+(p.scenes?.trim()?p.scenes.split('\n').filter(x=>x.trim()).length:sceneCount()),0)}
function invalidScenes(){return products.some(p=>p.scenes?.trim()&&p.scenes.split('\n').filter(x=>x.trim()).length>(config.max_scenes||12))}
function generationError(){
  const limit=config.max_images||250;
  if(!products.length)return 'Add at least 1 photo to begin';
  if(products.length>(config.max_products||50))return `Use at most ${config.max_products||50} photos per job`;
  if(!sceneCount()||sceneCount()>(config.max_scenes||12)||invalidScenes())return `Enter 1–${config.max_scenes||12} scenes per product`;
  if(assignedCount()>limit)return `Maximum ${limit} images per job. Remove photos or reduce scene counts.`;
  return '';
}
function recalc(){
  const n=sceneCount();
  $('scene-count').textContent=n+' scene'+(n!==1?'s':'');
  $('estimated-count').textContent=assignedCount().toLocaleString('en-IN');
  const error=generationError();
  $('generate-btn').disabled=isSubmitting||!!error;
  $('generation-hint').textContent=isSubmitting?'Submitting your image job…':error||`${products.length} product(s) · ${assignedCount()} images · ${config.mode==='live'?'Paid API requests':'Free demo previews'}`;
}

function requestKey(operation,fingerprint){
  if(pendingRequests[operation]?.fingerprint===fingerprint)return pendingRequests[operation].key;
  let key;
  if(globalThis.crypto.randomUUID)key=crypto.randomUUID();
  else{
    const bytes=crypto.getRandomValues(new Uint8Array(16));bytes[6]=(bytes[6]&15)|64;bytes[8]=(bytes[8]&63)|128;
    const hex=Array.from(bytes,b=>b.toString(16).padStart(2,'0')).join('');
    key=`${hex.slice(0,8)}-${hex.slice(8,12)}-${hex.slice(12,16)}-${hex.slice(16,20)}-${hex.slice(20)}`;
  }
  pendingRequests[operation]={fingerprint,key};return key;
}

function syncModelChoices(modelId,qualityId,preferredModel,preferredQuality){
  const models=config.models||[];if(!models.length)return;
  const modelSelector=$(modelId),qualitySelector=$(qualityId);
  const value=modelSelector.value||preferredModel;
  modelSelector.replaceChildren();
  for(const model of models){const opt=document.createElement('option');opt.value=model.id;opt.textContent=model.label||model.id;modelSelector.append(opt)}
  modelSelector.value=models.some(m=>m.id===value)?value:models.some(m=>m.id===preferredModel)?preferredModel:models[0].id;
  const updateQualities=()=>{
    const qualities=models.find(m=>m.id===modelSelector.value)?.qualities||[];
    const previous=qualitySelector.value||preferredQuality;
    qualitySelector.replaceChildren();
    for(const quality of qualities){const opt=document.createElement('option');opt.value=quality;opt.textContent=quality[0].toUpperCase()+quality.slice(1);qualitySelector.append(opt)}
    qualitySelector.value=qualities.includes(previous)?previous:qualities.includes(preferredQuality)?preferredQuality:qualities[0]||'';
  };
  modelSelector.onchange=updateQualities;updateQualities();
}

function showProducts(){
  const container=$('product-list');
  container.innerHTML='';
  if(!products.length){
    container.innerHTML='<div class="empty-sm">No products uploaded yet</div>';
    $('match-summary-pill').classList.add('hidden');
    recalc();
    return;
  }
  for(const item of products){
    const row=document.createElement('div');
    row.className='product-row';
    const m=item.matchedInfo;
    const matchBadge=m?
      `<div class="match-badge matched" title="${escapeHtml(m.title||'')}">✓ Matched: ${escapeHtml(m.sku)} ${m.asin?`(${escapeHtml(m.asin)})`:''} ${m.colour?`· ${escapeHtml(m.colour)}`:''} <small>via ${escapeHtml(m.method||'')}</small></div>`:
      `<div class="match-badge unmatched">Filename used as SKU · not in catalog</div>`;
    row.innerHTML=`<img src="${item.url}" alt="Product photograph">
      <div>
        <div class="product-meta-row">
          <small>${escapeHtml(item.file.name)} · ${(item.file.size/1024/1024).toFixed(1)} MB</small>
          ${matchBadge}
        </div>
        <input aria-label="SKU" placeholder="Enter item code / ASIN / SKU" value="${escapeHtml(item.sku)}">
        <textarea class="product-scene-override" rows="2" placeholder="Optional: custom scenes for this product, one per line. Blank uses default scenes.">${escapeHtml(item.scenes||'')}</textarea>
      </div>
      <button title="Remove photograph">×</button>`;
    row.querySelector('input').addEventListener('input',e=>{item.sku=e.target.value;item.skuEdited=true;item.matchedInfo=null});
    row.querySelector('textarea').addEventListener('input',e=>{item.scenes=e.target.value;recalc()});
    row.querySelector('button').addEventListener('click',()=>{
      URL.revokeObjectURL(item.url);
      products=products.filter(p=>p!==item);
      showProducts();
    });
    container.append(row);
  }
  recalc();
}

async function addFiles(files){
  const added=[];
  for(const file of files){
    if(products.length>=(config.max_products||50)){toast(`Maximum ${config.max_products||50} product photos per job.`,true);break}
    if(!file.type.startsWith('image/')){toast(`Unsupported file: ${file.name}`,true);continue}
    if(file.size>18*1024*1024){toast(`Too large: ${file.name}`,true);continue}
    const sku=file.name.replace(/\.[^.]+$/,'').replace(/[^\w-]/g,'_').slice(0,40);
    const product={file,sku,url:URL.createObjectURL(file),matchedInfo:null,skuEdited:false};
    products.push(product);added.push(product);
  }
  showProducts();
  if(added.length){
    matchProductsWithCatalog(added);
  }
}

async function matchProductsWithCatalog(candidates=products.filter(p=>!p.skuEdited)){
  const untouched=candidates.filter(p=>!p.skuEdited);
  if(!untouched.length)return;
  const filenames=untouched.map(p=>p.file.name);
  try{
    const res=await api('/api/catalog/match-files',{
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({filenames})
    });
    for(const p of untouched){
      if(!products.includes(p)||p.skuEdited)continue;
      const match=res.matches?.[p.file.name];
      if(match && match.matched){
        p.sku=match.sku;
        p.matchedInfo=match;
      }
    }
    const matchedCount=products.filter(p=>p.matchedInfo&&!p.skuEdited).length;
    const pill=$('match-summary-pill');
    if(matchedCount>0){
      pill.classList.remove('hidden');
      pill.innerHTML=`<span>✓ <b>${matchedCount} of ${products.length}</b> photos auto-matched to catalog specifications by filename.</span> <button class="ghost-btn" style="padding:4px 8px;margin:0;font-size:10px" onclick="setView('catalog')">View Catalog →</button>`;
    }else{
      pill.classList.add('hidden');
    }
    showProducts();
  }catch(e){
    console.error('Matching error:',e);
  }
}

async function loadConfig(){
  config=await api('/api/config');
  syncModelChoices('model','quality','gpt-image-2.5-flare','medium');
  syncModelChoices('revision-model','revision-quality','gpt-image-2.5-sunburst','high');
  syncModelChoices('aplus-model','aplus-quality','gpt-image-2.5-sunburst','high');
  for(const feature of ['aplus','usage'])$(`nav-${feature}`).classList.toggle('hidden',config.features?.[feature]===false);
  $('mode-label').textContent=config.mode==='demo'?'Demo mode · no API charges':'Live Image API · paid usage';
  $('mode-dot').classList.toggle('demo',config.mode==='demo');
  $('account-name').textContent=config.user||'Not signed in';
  $('account-label').textContent=config.mode==='demo'?'Preview workspace':'Team workspace';
  if(config.catalog_count!==undefined){
    $('catalog-count').textContent=config.catalog_count;
  }
  if(config.login_required&&!config.user)$('login-overlay').classList.remove('hidden');
  else $('login-overlay').classList.add('hidden');
  recalc();
}

async function loadStats(){
  try{
    const stats=await api('/api/stats');
    for(const key of ['generated','approved','pending','jobs'])$(`metric-${key}`).textContent=stats[key].toLocaleString('en-IN');
    $('library-count').textContent=stats.generated;
    if(stats.catalog!==undefined)$('catalog-count').textContent=stats.catalog;
  }catch(e){
    if(!config.login_required)toast(e.message,true);
  }
}

async function loadCatalog(q=''){
  const request=++catalogRequest;
  try{
    const data=await api('/api/catalog?q='+encodeURIComponent(q));
    if(request!==catalogRequest)return;
    catalogProducts=data.products||[];
    $('catalog-total-badge').textContent=data.total||0;
    $('catalog-count').textContent=data.total||0;
    const wrap=$('catalog-table-wrap');
    if(!catalogProducts.length){
      wrap.innerHTML='<div class="empty-sm">No products in catalog yet. Download the CSV template above and upload your sheet.</div>';
      return;
    }
    let html='<table class="cat-table"><thead><tr><th>SKU / ASIN</th><th>Title</th><th>Specs & Variation</th><th>Bullets & Highlight</th><th>Action</th></tr></thead><tbody>';
    for(const p of catalogProducts){
      const bullets=[p.bullet_1,p.bullet_2,p.bullet_3,p.bullet_4,p.bullet_5].filter(Boolean);
      html+=`<tr>
        <td>
          <strong>${escapeHtml(p.sku)}</strong>
          ${p.asin?`<br><small class="cat-badge">ASIN: ${escapeHtml(p.asin)}</small>`:''}
          ${p.item_code?`<br><small class="cat-badge">Code: ${escapeHtml(p.item_code)}</small>`:''}
        </td>
        <td><b>${escapeHtml(p.title||'Untitled')}</b></td>
        <td>
          ${p.colour?`<span class="cat-badge">Colour: ${escapeHtml(p.colour)}</span>`:''}
          ${p.size?`<span class="cat-badge">Size: ${escapeHtml(p.size)}</span>`:''}
          ${p.variation?`<br><small style="color:#677d8c">${escapeHtml(p.variation)}</small>`:''}
        </td>
        <td>
          ${p.item_highlight?`<div style="margin-bottom:4px;color:#186854"><b>Highlight:</b> ${escapeHtml(p.item_highlight)}</div>`:''}
          ${bullets.length?`<small style="color:#728394">• ${escapeHtml(bullets.slice(0,2).join(' • '))}${bullets.length>2?` (+${bullets.length-2} more)`:''}</small>`:''}
        </td>
        <td>
          <button class="cat-del-btn" title="Delete product" onclick="deleteCatalogItem('${escapeHtml(p.id)}')">🗑</button>
        </td>
      </tr>`;
    }
    html+='</tbody></table>';
    wrap.innerHTML=html;
  }catch(e){
    if(request===catalogRequest)toast('Catalog: '+e.message,true);
  }
}

async function deleteCatalogItem(id){
  if(!confirm('Delete this product from catalog?'))return;
  try{
    await api('/api/catalog/'+encodeURIComponent(id),{method:'DELETE'});
    toast('Product deleted from catalog');
    await loadCatalog($('catalog-search-input').value);
    await loadStats();
  }catch(e){
    toast(e.message,true);
  }
}

async function uploadCatalogCsv(file){
  if(!file)return;
  const form=new FormData();
  form.append('file',file);
  const statusBox=$('csv-status');
  statusBox.classList.remove('hidden');
  statusBox.textContent='Importing product data and updating Product Memory...';
  try{
    const res=await api('/api/catalog/import',{method:'POST',body:form});
    statusBox.textContent=`✓ Success! Imported ${res.imported} new products, updated ${res.updated}. Total in catalog: ${res.total}.`;
    toast(`Catalog imported: ${res.imported} new, ${res.updated} updated`);
    await loadCatalog();
    await loadStats();
  }catch(e){
    statusBox.textContent='⚠ Import failed: '+e.message;
    toast(e.message,true);
  }
}

async function loadRenders(){try{renderRecords=await api('/api/renders');drawGallery()}catch(e){if(!config.login_required)toast(e.message,true)}}
function drawGallery(){
  const filter=$('filter-status').value;
  const sku=$('filter-sku').value.toLowerCase();
  const records=renderRecords.filter(r=>(filter==='all'||filter==='approved'&&r.approved||r.status===filter)&&(`${r.sku} ${r.prompt}`.toLowerCase().includes(sku)));
  const container=$('gallery-content');container.innerHTML='';
  if(!records.length){
    container.innerHTML='<div class="placeholder">No images match your filters. Create your first production job to populate the gallery.</div>';
    return;
  }
  for(const r of records){
    const card=document.createElement('div');
    card.className='image-card';
    const statusLabel=r.status==='completed'?(r.approved?'Approved':'Needs review'):r.status==='failed'?'Failed':r.status;
    const modelDisplay=r.model.replace('gpt-image-2.5-','');
    card.innerHTML=`<div class="image-photo">${r.url?`<img src="${r.url}" loading="lazy" alt="${escapeHtml(r.sku)} generated image">`:`<span>${r.status==='failed'?'⚠':'◷'}</span>`}<span class="photo-status ${r.approved?'good':r.status==='failed'?'fail':''}">${escapeHtml(statusLabel)}</span></div>
      <div class="image-meta">
        <h3>${escapeHtml(r.sku)} · ${escapeHtml(r.kind.toUpperCase())}</h3>
        <div class="scene" title="${escapeHtml(r.prompt)}">${escapeHtml(r.prompt)}</div>
        <div class="details">${escapeHtml(r.user)} · ${escapeHtml(modelDisplay)} · ${escapeHtml(r.quality)}</div>
        <div class="card-buttons">${r.status==='completed'?`<button class="review-btn">Review / Revise</button><button class="approve-btn">${r.approved?'✓ Approved':'✓ Approve'}</button>`:`<small>${escapeHtml(r.error||'Processing...')}</small>`}</div>
      </div>`;
    const im=card.querySelector('.image-photo img');
    if(im)im.addEventListener('click',()=>openModal(r));
    card.querySelector('.review-btn')?.addEventListener('click',()=>openModal(r));
    card.querySelector('.approve-btn')?.addEventListener('click',()=>setApproval(r,!r.approved));
    container.append(card);
  }
}

async function loadJobs(){try{jobRecords=await api('/api/jobs');drawJobs()}catch(e){if(!config.login_required)toast(e.message,true)}}
function drawJobs(){
  const el=$('queue-list');el.innerHTML='';
  if(!jobRecords.length){
    el.innerHTML='<div class="placeholder">Your production queue is empty. Start by generating images.</div>';
    return;
  }
  for(const j of jobRecords){
    const percent=Math.round((j.completed+j.failed)/Math.max(1,j.total)*100);
    const card=document.createElement('div');card.className='queue-card';
    card.innerHTML=`<div class="queue-head"><div><strong>${escapeHtml(j.kind.toUpperCase())} · ${j.total} images</strong><small>Created by ${escapeHtml(j.user)} · ${new Date(j.created_at).toLocaleString()}</small></div><div><b>${j.completed}/${j.total}</b> <small>completed</small></div></div><div class="progress"><div style="width:${percent}%"></div></div><p>${j.failed?`${j.failed} failed · `:''}${percent===100?'Job finished':'Processing in queue'} · ${percent}% done</p>`;
    el.append(card);
  }
}

async function generate(){
  if(isSubmitting)return;
  const error=generationError();if(error)return toast(error,true);
  const count=assignedCount();
  if(config.mode==='live'&&count>10&&!confirm(`Submit ${count} PAID image generations? Every generation/edit may incur API fees.`))return;
  const snapshot={products:products.map(p=>({file:p.file,sku:p.sku,scenes:p.scenes||''})),kind};
  for(const field of ['scenes','model','quality','canvas'])snapshot[field]=$(field).value;
  const fingerprint=JSON.stringify({...snapshot,products:snapshot.products.map(p=>{
    if(!fileIds.has(p.file))fileIds.set(p.file,++nextFileId);
    return {file:fileIds.get(p.file),sku:p.sku,scenes:p.scenes};
  })});
  const key=requestKey('generate',fingerprint);
  const payload=new FormData();
  for(const p of snapshot.products)payload.append('files',p.file,p.file.name);
  payload.append('skus',JSON.stringify(snapshot.products.map(x=>x.sku)));
  payload.append('scene_sets',JSON.stringify(snapshot.products.map(x=>x.scenes)));
  for(const field of ['scenes','model','quality','canvas'])payload.append(field,snapshot[field]);
  payload.append('kind',snapshot.kind);
  isSubmitting=true;recalc();
  $('generate-btn').textContent='Submitting...';
  try{
    const result=await api('/api/generate',{method:'POST',headers:{'Idempotency-Key':key},body:payload});
    pendingRequests.generate=null;
    toast(`${result.images_queued} images queued · ${result.mode==='demo'?'demo previews only':'live API generation'}`);
    setView('jobs');
    await Promise.all([loadJobs(),loadStats()]);
  }catch(e){
    toast(e.message,true);
  }finally{
    isSubmitting=false;
    $('generate-btn').innerHTML='✦ Generate images <span>→</span>';
    recalc();
  }
}

async function setApproval(r,approved){
  try{
    await api(`/api/renders/${r.id}/approve`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({approved})});
    r.approved=approved;
    toast(approved?'Image approved':'Approval removed');
    drawGallery();
    loadStats();
    if(selected?.id===r.id)showModalButton();
  }catch(e){toast(e.message,true)}
}
function showModalButton(){$('approve-modal-btn').textContent=selected?.approved?'✓ Approved (click to undo)':'✓ Approve image'}
function closeModal(){
  $('modal').classList.add('hidden');historyRequest++;
  if(modalReturnFocus?.isConnected)modalReturnFocus.focus();
}
function modalKeydown(e){
  if($('modal').classList.contains('hidden'))return;
  if(e.key==='Escape'){e.preventDefault();closeModal();return}
  if(e.key!=='Tab')return;
  const focusable=[...$('modal').querySelectorAll('button:not(:disabled), a[href], input:not(:disabled), select:not(:disabled), textarea:not(:disabled), [tabindex="0"]')].filter(el=>el.getClientRects().length);
  if(!focusable.length){e.preventDefault();return}
  const first=focusable[0],last=focusable[focusable.length-1];
  if(e.shiftKey&&(document.activeElement===first||!$('modal').contains(document.activeElement))){e.preventDefault();last.focus()}
  else if(!e.shiftKey&&(document.activeElement===last||!$('modal').contains(document.activeElement))){e.preventDefault();first.focus()}
}
function openModal(r){
  if($('modal').classList.contains('hidden'))modalReturnFocus=document.activeElement;
  selected=r;
  $('remember-revision').checked=false;
  loadRevisionHistory(r.id);
  $('modal-title').textContent=`${r.sku} · ${r.kind.toUpperCase()}`;
  $('modal-img').src=r.url;
  $('modal-status').textContent=`${r.model} · ${r.quality} quality · ${r.approved?'Approved':'Not approved'}`;
  $('revision-prompt').value='';
  $('download-modal').href=r.url;
  $('download-modal').download=`${r.sku}_${r.id.slice(0,6)}.jpg`;
  showModalButton();
  $('modal').classList.remove('hidden');
  $('close-modal').focus();
}

async function revise(){
  if(!selected||isRevising)return;
  const text=$('revision-prompt').value.trim();
  if(text.length<5)return toast('Please explain the correction first.',true);
  if(config.mode==='live'&&!confirm('Send a new PAID image edit request?'))return;
  const snapshot={render_id:selected.id,instructions:text,model:$('revision-model').value,quality:$('revision-quality').value,remember_rule:$('remember-revision').checked};
  const key=requestKey('revise',JSON.stringify(snapshot));
  isRevising=true;
  $('revise-btn').disabled=true;
  try{
    const result=await api('/api/revise',{method:'POST',headers:{'Content-Type':'application/json','Idempotency-Key':key},body:JSON.stringify(snapshot)});
    pendingRequests.revise=null;
    if(selected?.id===snapshot.render_id)closeModal();
    toast(result.pending_rule?'Revision queued. The correction will be saved when you approve the result.':result.saved_rule?'Revision queued and saved as permanent product rule':result.mode==='demo'?'Demo revision created':'Revision queued for generation');
    setView('jobs');
    await loadJobs();
  }catch(e){toast(e.message,true)}
  finally{isRevising=false;$('revise-btn').disabled=false}
}

async function loadRevisionHistory(id){
  const request=++historyRequest;
  const el=$('revision-history');el.textContent='Loading version history...';
  try{
    const hist=await api(`/api/renders/${encodeURIComponent(id)}/history`);
    if(request!==historyRequest||selected?.id!==id)return;
    el.innerHTML='<b>Revision history · '+hist.length+' version(s)</b>';
    for(const [i,v] of hist.entries()){
      const row=document.createElement('div');row.className='revision-item';
      row.innerHTML=`<span>v${i+1} · ${escapeHtml(v.user)} · ${new Date(v.created_at).toLocaleString()}</span><small>${escapeHtml(v.prompt)}</small>`;
      const rules=v.memory_snapshot?.rules||[];
      const tag=document.createElement('small');tag.textContent=`Memory applied: ${rules.length} saved SKU correction(s) · Brand: ${v.memory_snapshot?.branding?.name||'none'}`;row.append(tag);
      if(v.url && v.id!==id){
        const btn=document.createElement('button');btn.className='revision-link';btn.textContent='View this version';
        btn.addEventListener('click',async()=>{await loadRenders();const rec=renderRecords.find(x=>x.id===v.id);if(rec)openModal(rec);else window.open(v.url,'_blank')});
        row.append(btn);
      }
      el.append(row);
    }
  }catch(e){if(request===historyRequest)el.textContent='Could not load revision history: '+e.message}
}

async function loadMemoryHome(){
  try{
    const company=await api('/api/memory/company');$('company-rules').value=company.instructions;
    $('company-rules').disabled=!company.can_edit;$('company-save').disabled=!company.can_edit;
    $('company-edit-note').textContent=company.can_edit?'Owner access: Changes apply to NEW image jobs for all team members.':'Read-only: Ask your workspace owner to change company instructions.';
    const saved=await api('/api/memory/products'); const area=$('memory-products');area.innerHTML='';
    if(!saved.length)area.innerHTML='<div class="empty-sm">No product facts saved yet. Start with a SKU above or import catalog CSV.</div>';
    for(const product of saved){
      const b=document.createElement('button');b.className='memory-product-btn';
      b.textContent=`${product.sku}${product.brand_name?' · '+product.brand_name:''} · ${product.rule_count} rule(s)`;
      b.onclick=()=>openProductMemory(product.sku);
      area.append(b);
    }
  }catch(e){toast('Memory: '+e.message,true)}
}

async function openProductMemory(sku){
  const key=String(sku||'').trim().toUpperCase();if(!key)return toast('Enter an SKU / ASIN',true);
  const request=++memoryRequest;
  try{
    const record=await api('/api/memory/products/'+encodeURIComponent(key));
    if(request!==memoryRequest)return;
    currentMemorySku=record.sku;currentMemory=record;
    $('memory-sku').value=record.sku;$('memory-current-sku').textContent=record.sku;
    $('memory-facts').value=record.facts;
    $('catalog-facts').textContent=record.catalog_facts||'';
    $('catalog-facts-panel').classList.toggle('hidden',!record.catalog_facts);
    $('memory-updated').textContent=record.updated_by?`Saved by ${record.updated_by}`:'New product';
    $('memory-details').classList.remove('hidden');
    await refreshBrands(record.brand_id||'');
    if(request!==memoryRequest)return;
    const el=$('memory-rule-list');el.innerHTML='';
    if(!record.rules.length)el.innerHTML='<div class="empty-sm">No permanent corrections saved for this SKU.</div>';
    for(const rule of record.rules){
      const row=document.createElement('div');row.className='memory-rule';
      const content=document.createElement('div');const t=document.createElement('strong');t.textContent=rule.instruction;
      const sub=document.createElement('small');sub.textContent=`Added by ${rule.created_by}`;content.append(t,sub);row.append(content);
      if(rule.can_delete){
        const b=document.createElement('button');b.className='rule-delete';b.textContent='Remove';
        b.addEventListener('click',async()=>{
          if(!confirm('Remove this rule for future generations?'))return;
          try{await api(`/api/memory/products/${encodeURIComponent(record.sku)}/rules/${rule.id}`,{method:'DELETE'});if(currentMemorySku===record.sku)await openProductMemory(record.sku);toast('Rule removed.')}catch(e){toast(e.message,true)}
        });
        row.append(b);
      }
      el.append(row);
    }
  }catch(e){if(request===memoryRequest)toast(e.message,true)}
}

async function refreshBrands(focusId=''){
  const request=++brandRequest,sku=currentMemorySku,memoryVersion=memoryRequest;
  const profiles=await api('/api/memory/brands');
  if(request!==brandRequest||sku!==currentMemorySku||memoryVersion!==memoryRequest)return;
  brandProfiles=profiles;
  const selector=$('memory-brand-select');selector.innerHTML='<option value="">— No brand selected —</option>';
  for(const b of brandProfiles){const opt=document.createElement('option');opt.value=b.id;opt.textContent=b.name;selector.append(opt)}
  const assigned=currentMemory?.brand_id||'';
  selector.value=focusId||assigned;
  if(selector.value!==String(focusId||assigned))selector.value='';
  brandEditorNew=false;drawBrandProfile();
}

function drawBrandProfile(){
  const bid=$('memory-brand-select').value;
  const b=brandEditorNew?null:brandProfiles.find(x=>x.id===bid);
  const editable=!!currentMemory?.can_manage_branding;
  const values={name:'',heading_font:'Cinzel',body_font:'Roboto',primary_color:'#233A54',secondary_color:'#F2EEE8',accent_color:'#B98E5C',visual_style:'',brand_rules:'',...(b||{})};
  for(const [id,key] of Object.entries({'brand-name':'name','brand-heading-font':'heading_font','brand-body-font':'body_font','brand-primary-color':'primary_color','brand-secondary-color':'secondary_color','brand-accent-color':'accent_color','brand-style':'visual_style','brand-rules':'brand_rules'})){
    $(id).value=values[key]||'';$(id).disabled=!editable;
  }
  const url=b?.logo_url||'';
  $('brand-logo-preview').classList.toggle('hidden',!url);
  $('brand-logo-empty').classList.toggle('hidden',!!url);
  if(url)$('brand-logo-preview').src=url+'?v='+encodeURIComponent(b.updated_at||'');
  else $('brand-logo-preview').removeAttribute('src');
  $('memory-brand-assign').disabled=!editable;
  $('memory-brand-new').disabled=!editable;
  $('brand-save-profile').disabled=!editable;
  $('brand-logo-file').disabled=!editable||!b;
  $('brand-upload-logo').disabled=!editable||!b;
  $('brand-access-note').textContent=editable?(brandEditorNew?'Save to create this brand and assign it to your SKU.':'Changes to this brand affect all SKUs assigned to it.'):'Read-only profile. Ask your workspace owner to update brand rules.';
  const assigned=brandProfiles.find(x=>x.id===currentMemory?.brand_id);
  $('brand-assigned-status').textContent=assigned?'Assigned: '+assigned.name:'No brand assigned';
}

function newBrandProfile(){
  if(!currentMemory?.can_manage_branding)return;
  $('memory-brand-select').value='';brandEditorNew=true;drawBrandProfile();
  $('brand-name').focus();
}

function currentBrandBody(){return {
  name:$('brand-name').value.trim(),heading_font:$('brand-heading-font').value.trim(),body_font:$('brand-body-font').value.trim(),
  primary_color:$('brand-primary-color').value,secondary_color:$('brand-secondary-color').value,accent_color:$('brand-accent-color').value,
  visual_style:$('brand-style').value.trim(),brand_rules:$('brand-rules').value.trim()
}}

async function saveBrandProfile(){
  if(!currentMemorySku)return toast('Open a product first',true);
  const sku=currentMemorySku;
  const bid=brandEditorNew?'':$('memory-brand-select').value;
  const body=currentBrandBody();
  if(body.name.length<2)return toast('Enter a brand name',true);
  try{
    const record=await api(bid?`/api/memory/brands/${encodeURIComponent(bid)}`:'/api/memory/brands',{
      method:bid?'PUT':'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)
    });
    const savedId=record.id;
    if(!bid){
      await api(`/api/memory/products/${encodeURIComponent(sku)}/brand`,{
        method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({brand_id:savedId})
      });
      if(currentMemorySku===sku)currentMemory.brand_id=savedId;
    }
    if(currentMemorySku===sku)await refreshBrands(savedId);
    await loadMemoryHome();
    toast(bid?'Brand profile updated for all assigned SKUs':'Brand created and assigned to '+sku);
  }catch(e){toast(e.message,true)}
}

async function assignProductBrand(){
  if(!currentMemorySku)return toast('Open a product first',true);
  if(brandEditorNew)return toast('Save your new brand before assigning it.',true);
  const bid=$('memory-brand-select').value;
  const sku=currentMemorySku;
  try{
    await api(`/api/memory/products/${encodeURIComponent(sku)}/brand`,{
      method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({brand_id:bid||null})
    });
    if(currentMemorySku===sku){currentMemory.brand_id=bid;drawBrandProfile()}
    await loadMemoryHome();
    toast(bid?'Brand assigned to '+sku:'Brand removed from SKU.');
  }catch(e){toast(e.message,true)}
}

async function uploadBrandLogo(){
  const bid=$('memory-brand-select').value;
  const sku=currentMemorySku;
  const file=$('brand-logo-file').files[0];
  if(!bid||brandEditorNew)return toast('Save a brand profile before uploading a logo',true);
  if(!file)return toast('Choose a logo file first',true);
  const form=new FormData();form.append('file',file);
  try{
    await api(`/api/memory/brands/${encodeURIComponent(bid)}/logo`,{method:'POST',body:form});
    if(currentMemorySku===sku&&$('memory-brand-select').value===bid){$('brand-logo-file').value='';await refreshBrands(bid)}
    toast('Logo stored in brand profile');
  }catch(e){toast(e.message,true)}
}

async function saveProductFacts(){
  if(!currentMemorySku)return toast('Open a product first',true);
  const sku=currentMemorySku,facts=$('memory-facts').value;
  try{
    await api('/api/memory/products/'+encodeURIComponent(sku),{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({facts})});
    toast('Product facts saved');
    if(currentMemorySku===sku)await openProductMemory(sku);
    await loadMemoryHome();
  }catch(e){toast(e.message,true)}
}

async function saveProductRule(){
  if(!currentMemorySku)return toast('Open a product first',true);
  const v=$('memory-new-rule').value.trim();if(v.length<5)return toast('Enter a meaningful correction',true);
  const sku=currentMemorySku;
  try{
    await api('/api/memory/products/'+encodeURIComponent(sku)+'/rules',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({instruction:v})});
    if(currentMemorySku===sku){$('memory-new-rule').value='';await openProductMemory(sku)}
    toast('Permanent correction saved for '+sku);
    await loadMemoryHome();
  }catch(e){toast(e.message,true)}
}

async function saveCompanyRules(){
  const text=$('company-rules').value.trim();if(text.length<15)return toast('Instructions are too short',true);
  if(!confirm('Apply updated master rules to all FUTURE image requests?'))return;
  try{
    await api('/api/memory/company',{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({instructions:text})});
    toast('Company-wide rules saved');
  }catch(e){toast(e.message,true)}
}

function attachEvents(){
  const z=$('drop-zone');
  $('photos').addEventListener('change',e=>{addFiles([...e.target.files]);e.target.value=''});
  for(const event of ['dragenter','dragover'])z.addEventListener(event,e=>{e.preventDefault();z.classList.add('dragging')});
  for(const event of ['dragleave','drop'])z.addEventListener(event,e=>{e.preventDefault();z.classList.remove('dragging')});
  z.addEventListener('drop',e=>addFiles([...e.dataTransfer.files]));
  
  // CSV drop zone
  const cz=$('csv-drop-zone');
  $('csv-file').addEventListener('change',e=>{if(e.target.files[0])uploadCatalogCsv(e.target.files[0]);e.target.value=''});
  for(const event of ['dragenter','dragover'])cz.addEventListener(event,e=>{e.preventDefault();cz.classList.add('dragging')});
  for(const event of ['dragleave','drop'])cz.addEventListener(event,e=>{e.preventDefault();cz.classList.remove('dragging')});
  cz.addEventListener('drop',e=>{if(e.dataTransfer.files[0])uploadCatalogCsv(e.dataTransfer.files[0])});
  
  let searchTimer;
  $('catalog-search-input')?.addEventListener('input',e=>{
    clearTimeout(searchTimer);
    searchTimer=setTimeout(()=>loadCatalog(e.target.value),300);
  });
  
  for(const button of document.querySelectorAll('.type-option'))button.addEventListener('click',()=>{
    kind=button.dataset.kind;
    for(const x of document.querySelectorAll('.type-option'))x.classList.toggle('selected',x===button);
    $('scenes').value=SCENES[kind].join('\n');
    $('task-tip').textContent=TIP[kind];
    recalc();
  });
  
  $('scenes').addEventListener('input',recalc);
  $('generate-btn').addEventListener('click',generate);
  for(const view of ['create','catalog','gallery','jobs','memory','aplus','usage']){
    $(`nav-${view}`)?.addEventListener('click',()=>setView(view));
  }
  
  $('see-queue').addEventListener('click',()=>setView('jobs'));
  $('refresh-btn').addEventListener('click',refresh);
  $('queue-refresh').addEventListener('click',loadJobs);
  $('gallery-refresh').addEventListener('click',loadRenders);
  $('filter-status').addEventListener('change',drawGallery);
  $('filter-sku').addEventListener('input',drawGallery);
  $('close-modal').addEventListener('click',closeModal);
  $('modal').addEventListener('click',e=>{if(e.target===$('modal'))closeModal()});
  document.addEventListener('keydown',modalKeydown);
  $('revise-btn').addEventListener('click',revise);
  $('approve-modal-btn').addEventListener('click',()=>selected&&setApproval(selected,!selected.approved));
  
  $('login-form').addEventListener('submit',async e=>{
    e.preventDefault();$('login-error').textContent='';
    try{
      const result=await api('/api/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({username:$('login-user').value,password:$('login-pass').value})});
      config.user=result.user;
      $('login-overlay').classList.add('hidden');
      $('account-name').textContent=result.user;
      toast('Signed in');
      refresh();
    }catch(err){$('login-error').textContent=err.message}
  });
  
  $('memory-load').addEventListener('click',()=>openProductMemory($('memory-sku').value));
  $('memory-sku').addEventListener('keydown',e=>{if(e.key==='Enter')openProductMemory(e.target.value)});
  $('memory-save-facts').addEventListener('click',saveProductFacts);
  $('memory-add-rule').addEventListener('click',saveProductRule);
  $('company-save').addEventListener('click',saveCompanyRules);
  $('memory-brand-select').addEventListener('change',()=>{brandEditorNew=false;drawBrandProfile()});
  $('memory-brand-assign').addEventListener('click',assignProductBrand);
  $('memory-brand-new').addEventListener('click',newBrandProfile);
  $('brand-save-profile').addEventListener('click',saveBrandProfile);
  $('brand-upload-logo').addEventListener('click',uploadBrandLogo);
  $('open-memory').addEventListener('click',()=>{setView('memory');if(products[0]?.sku)openProductMemory(products[0].sku)});
  $('logout-btn').addEventListener('click',async()=>{await api('/api/logout',{method:'POST'});location.reload()});
  window.StudioFlowAplus?.bind();
  window.StudioFlowUsage?.bind();
}

async function refresh(){await Promise.allSettled([loadStats(),loadJobs(),loadRenders()]);if(visible==='usage')await window.StudioFlowUsage?.load()}
async function init(){
  attachEvents();
  $('scenes').value=SCENES.lifestyle.join('\n');
  try{
    await loadConfig();
    if(!config.login_required||config.user)await refresh();
  }catch(e){toast('Could not connect to app server: '+e.message,true)}
  const poll=async()=>{
    try{
      if(document.hidden||(config.login_required&&!config.user))return;
      const requests=[loadStats()];
      if(visible==='jobs')requests.push(loadJobs());
      if(visible==='gallery')requests.push(loadRenders());
      if(visible==='usage')requests.push(window.StudioFlowUsage?.load());
      await Promise.allSettled(requests);
    }finally{setTimeout(poll,5000)}
  };
  setTimeout(poll,5000);
}
init();
