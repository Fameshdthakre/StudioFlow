"""Shared timestamps, audit records and workspace record checks."""

import uuid
from datetime import datetime, timezone

from fastapi import HTTPException


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def audit(c, user, event, sku, content):
    c.execute(
        "INSERT INTO memory_audit VALUES (?,?,?,?,?,?)",
        (uuid.uuid4().hex, user, event, sku, content[:5000], now()),
    )


def owned(r, user):
    if not r:
        raise HTTPException(404, "Not found")
    return dict(r)
