"""Dedicated A+ profiles; generation uses immutable copies of these records."""
import io
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from PIL import Image, ImageOps
from pydantic import BaseModel, ConfigDict, Field
import sqlite3

DEFAULT_MASTER = (
    'Create an Amazon Premium A+ banner background using the exact source product. '
    'Preserve its shape, colour, texture, labels and scale. Leave clear space for '
    'headlines and feature copy. Use relevant environments and consistent brand colours. '
    'Do not render text, numbers, logos, divider lines or invented product features. '
    'Approved typography and logo artwork are applied separately. No children.'
)


class ProfileInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    name: str = Field(min_length=2, max_length=100)
    master_instructions: str = Field(default=DEFAULT_MASTER, min_length=15, max_length=9000)
    branding_notes: str = Field(default='', max_length=5000)
    heading_font: str = Field(default='Cinzel', min_length=1, max_length=100)
    body_font: str = Field(default='Roboto', min_length=1, max_length=100)
    primary_color: str = Field(default='#283F44', pattern=r'^#[0-9a-fA-F]{6}$')
    secondary_color: str = Field(default='#F4E7D9', pattern=r'^#[0-9a-fA-F]{6}$')
    accent_color: str = Field(default='#9E7655', pattern=r'^#[0-9a-fA-F]{6}$')


def snapshot(connection, profile_id):
    row = connection.execute('SELECT * FROM aplus_profiles WHERE id=?', (profile_id,)).fetchone()
    if not row:
        raise HTTPException(400, 'Choose a saved A+ profile before generating')
    return dict(row)


def prompt(profile):
    return (
        'A+ PROFILE — background composition and styling only:\n'
        f'Profile: {profile["name"]}\n'
        f'Colours: {profile["primary_color"]}, {profile["secondary_color"]}, {profile["accent_color"]}\n'
        f'Downstream typography: {profile["heading_font"]} / {profile["body_font"]}\n'
        f'Branding notes: {profile["branding_notes"]}\n'
        f'A+ instructions: {profile["master_instructions"]}\n'
        'The logo is stored as a reference for later composition. Do not draw a logo or render words. '
        'The real product and verified SKU facts remain authoritative.'
    )


def build_router(db, current_user, is_admin, logo_dir: Path, enabled, audit):
    def require_feature():
        if not enabled():
            raise HTTPException(404, 'A+ Studio is disabled')

    router = APIRouter(prefix='/api/aplus', dependencies=[Depends(require_feature)])

    def owner(user):
        if not is_admin(user):
            raise HTTPException(403, 'Only a workspace owner can change A+ profiles')

    def payload(row):
        result = dict(row)
        result['logo_url'] = f'/api/aplus/profiles/{result["id"]}/logo' if result.pop('logo_file') else ''
        return result

    @router.get('/profiles')
    def profiles(user=Depends(current_user)):
        with db() as c:
            rows = c.execute('SELECT * FROM aplus_profiles ORDER BY name').fetchall()
        return {'profiles': [payload(row) for row in rows], 'can_edit': is_admin(user), 'default_master': DEFAULT_MASTER}

    @router.post('/profiles')
    def create(body: ProfileInput, user=Depends(current_user)):
        owner(user)
        pid = uuid.uuid4().hex
        values = body.model_dump()
        with db() as c:
            try:
                c.execute('INSERT INTO aplus_profiles(id,name,master_instructions,branding_notes,heading_font,body_font,primary_color,secondary_color,accent_color,updated_at,updated_by) '
                          'VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                          (pid, *values.values(), datetime.now(timezone.utc).isoformat(), user))
            except sqlite3.IntegrityError:
                raise HTTPException(409, 'An A+ profile with this name already exists')
            audit(c, user, 'aplus_profile_created', '', pid)
        return {'id': pid, 'ok': True}

    @router.put('/profiles/{pid}')
    def update(pid: str, body: ProfileInput, user=Depends(current_user)):
        owner(user)
        with db() as c:
            c.execute('BEGIN IMMEDIATE')
            if not c.execute('SELECT 1 FROM aplus_profiles WHERE id=?', (pid,)).fetchone():
                raise HTTPException(404, 'A+ profile not found')
            try:
                c.execute('UPDATE aplus_profiles SET name=?,master_instructions=?,branding_notes=?,heading_font=?,body_font=?,primary_color=?,secondary_color=?,accent_color=?,updated_at=?,updated_by=? WHERE id=?',
                          (*body.model_dump().values(), datetime.now(timezone.utc).isoformat(), user, pid))
            except sqlite3.IntegrityError:
                raise HTTPException(409, 'An A+ profile with this name already exists')
            audit(c, user, 'aplus_profile_updated', '', pid)
        return {'id': pid, 'ok': True}

    @router.post('/profiles/{pid}/logo')
    def upload_logo(pid: str, file: UploadFile = File(...), user=Depends(current_user)):
        owner(user)
        with db() as c:
            if not c.execute('SELECT 1 FROM aplus_profiles WHERE id=?', (pid,)).fetchone():
                raise HTTPException(404, 'A+ profile not found')
        raw = file.file.read(5 * 1024 * 1024 + 1)
        if len(raw) > 5 * 1024 * 1024:
            raise HTTPException(413, 'Logo must be under 5 MB')
        filename = uuid.uuid4().hex + '.png'
        target = logo_dir / filename
        try:
            try:
                with Image.open(io.BytesIO(raw)) as image:
                    if image.format not in ('PNG', 'JPEG', 'WEBP') or min(image.size) < 16 or image.width * image.height > 16_000_000:
                        raise ValueError('Invalid image')
                    image = ImageOps.exif_transpose(image).convert('RGBA')
                    image.thumbnail((1800, 1800), Image.Resampling.LANCZOS)
                    image.save(target, 'PNG')
            except Exception as exc:
                raise HTTPException(400, 'Upload a valid PNG, JPG or WEBP logo') from exc
            with db() as c:
                c.execute('UPDATE aplus_profiles SET logo_file=?,updated_at=?,updated_by=? WHERE id=?',
                          (filename, datetime.now(timezone.utc).isoformat(), user, pid))
                audit(c, user, 'aplus_logo_uploaded', '', pid)
        except BaseException:
            target.unlink(missing_ok=True)
            raise
        # Retain previous logos: an accepted job's profile snapshot may refer to one.
        return {'ok': True, 'logo_url': f'/api/aplus/profiles/{pid}/logo'}

    @router.get('/profiles/{pid}/logo')
    def logo(pid: str, user=Depends(current_user)):
        with db() as c:
            row = c.execute('SELECT logo_file FROM aplus_profiles WHERE id=?', (pid,)).fetchone()
        name = row['logo_file'] if row else ''
        if not re.fullmatch(r'[0-9a-f]{32}\.png', name) or not (logo_dir / name).is_file():
            raise HTTPException(404, 'A+ logo not found')
        return FileResponse(logo_dir / name, media_type='image/png')

    return router
