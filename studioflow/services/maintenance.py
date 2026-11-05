"""Shared maintenance gate and auditable operational state."""

from fastapi import HTTPException
from .common import now
from contextlib import contextmanager
from functools import lru_cache
from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool


@lru_cache(maxsize=4)
def gate_engine(url):
    # Request gates must not exhaust the pool used by feature transactions.
    return create_engine(url, poolclass=NullPool, connect_args={"connect_timeout": 10})


@contextmanager
def writer(workspace):
    if not workspace.settings.database_url:
        with workspace.db() as c:
            require_writable(c)
        yield
        return
    with gate_engine(workspace.settings.database_url).connect() as c:
        locked = c.execute(
            text("SELECT pg_try_advisory_lock_shared(193700, 2)")
        ).scalar()
        if not locked:
            raise HTTPException(503, "Workspace maintenance is active")
        try:
            with workspace.db() as db:
                require_writable(db)
            yield
        finally:
            c.execute(text("SELECT pg_advisory_unlock_shared(193700, 2)"))


@contextmanager
def exclusive(workspace):
    if not workspace.settings.database_url:
        raise RuntimeError("Consistent operational snapshots require PostgreSQL")
    with gate_engine(workspace.settings.database_url).connect() as c:
        c.execute(text("SET lock_timeout='600s'"))
        c.execute(text("SELECT pg_advisory_lock(193700, 2)"))
        try:
            yield
        finally:
            c.execute(text("SELECT pg_advisory_unlock(193700, 2)"))


def state(workspace):
    with workspace.db() as c:
        rows = c.execute("SELECT key,value,updated_at FROM operations").fetchall()
    return {
        r["key"]: {"value": r["value"], "updated_at": r["updated_at"]} for r in rows
    }


def paused(c):
    row = c.execute("SELECT value FROM operations WHERE key='maintenance'").fetchone()
    return bool(row and row[0] == "1")


def require_writable(c):
    if paused(c):
        raise HTTPException(
            503, "Workspace maintenance is active; please retry after it finishes"
        )


def set_value(workspace, key, value):
    with workspace.db() as c:
        c.execute("BEGIN IMMEDIATE")
        c.execute(
            "INSERT INTO operations(key,value,updated_at) VALUES(?,?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at",
            (key, str(value), now()),
        )
