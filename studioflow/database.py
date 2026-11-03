"""SQLite connections and additive, versioned schema migrations."""

import sqlite3
from contextlib import contextmanager
from pathlib import Path

from .migrations import MIGRATIONS
from .migrations.base import SCHEMA


@contextmanager
def connect(path):
    con = sqlite3.connect(path, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys=ON")
    try:
        yield con
        con.commit()
    except BaseException:
        con.rollback()
        raise
    finally:
        con.close()


def initialize(path):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with connect(path) as c:
        c.execute("PRAGMA journal_mode=WAL")
        c.executescript(SCHEMA)
        c.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations(version INTEGER PRIMARY KEY)"
        )
        c.execute("BEGIN IMMEDIATE")
        for version, apply in MIGRATIONS:
            if not c.execute(
                "SELECT 1 FROM schema_migrations WHERE version=?", (version,)
            ).fetchone():
                if apply(c):
                    c.execute(
                        "INSERT INTO schema_migrations(version) VALUES(?)", (version,)
                    )
