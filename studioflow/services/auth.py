"""Persistent workspace sessions."""

import secrets
from datetime import datetime, timedelta, timezone

from .common import now


def get_session_user(workspace, token):
    if not token:
        return None
    with workspace.db() as c:
        row = c.execute(
            "SELECT user, expires_at FROM sessions WHERE token=?", (token,)
        ).fetchone()
        if not row:
            return None
        if row["user"] not in workspace.settings.team or row["expires_at"] < now():
            return None
        return row["user"]


def create_session(workspace, user, max_age_days=7):
    token = secrets.token_urlsafe(32)
    created = now()
    expires = (datetime.now(timezone.utc) + timedelta(days=max_age_days)).isoformat(
        timespec="seconds"
    )
    with workspace.db() as c:
        c.execute(
            "INSERT INTO sessions (token, user, created_at, expires_at) VALUES (?, ?, ?, ?)",
            (token, user, created, expires),
        )
    return token


def delete_session(workspace, token):
    if token:
        with workspace.db() as c:
            c.execute("DELETE FROM sessions WHERE token=?", (token,))
