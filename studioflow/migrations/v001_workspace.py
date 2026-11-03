"""Existing schema migration 1; version and SQL preserved."""


def apply(c):
    additions = {
        "renders": {
            "memory_snapshot": "TEXT DEFAULT ''",
            "worker_id": "TEXT DEFAULT ''",
            "pending_rule": "TEXT DEFAULT ''",
        },
        "product_memory": {"brand_id": "TEXT DEFAULT ''"},
    }
    for table, columns in additions.items():
        existing = {row["name"] for row in c.execute(f"PRAGMA table_info({table})")}
        for name, declaration in columns.items():
            if name not in existing:
                c.execute(f"ALTER TABLE {table} ADD COLUMN {name} {declaration}")
    c.execute(
        "CREATE TABLE IF NOT EXISTS workers(id TEXT PRIMARY KEY, heartbeat REAL NOT NULL)"
    )
    c.execute(
        "CREATE TABLE IF NOT EXISTS idempotency(user TEXT NOT NULL, key TEXT NOT NULL, route TEXT NOT NULL, fingerprint TEXT NOT NULL, response TEXT NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY(user,key))"
    )
    c.execute(
        "CREATE INDEX IF NOT EXISTS idx_renders_status ON renders(status,created_at)"
    )
    return True
