"""Transactional request deduplication for paid generation submissions."""
import hashlib
import json

from fastapi import HTTPException


def validate_key(key):
    if key is not None and (not 8 <= len(key) <= 128 or not key.isascii() or not all(c.isalnum() or c in '-_.' for c in key)):
        raise HTTPException(400, 'Idempotency-Key must contain 8–128 letters, digits, hyphens, underscores or dots')


def fingerprint(payload, images=()):
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode())
    for raw in images:
        digest.update(hashlib.sha256(raw).digest())
    return digest.hexdigest()


def replay(connection, user, key, route, digest):
    if not key:
        return None
    row = connection.execute('SELECT * FROM idempotency WHERE user=? AND key=?', (user, key)).fetchone()
    if row is None:
        return None
    if row['route'] != route or row['fingerprint'] != digest:
        raise HTTPException(409, 'This Idempotency-Key was already used for a different request')
    return json.loads(row['response'])


def remember(connection, user, key, route, digest, result, created_at):
    if key:
        connection.execute('INSERT INTO idempotency VALUES(?,?,?,?,?,?)',
                           (user, key, route, digest, json.dumps(result), created_at))
