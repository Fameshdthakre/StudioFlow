"""Stable identity and asset-reference repositories shared by feature services."""

import uuid


def ensure_user(c, username):
    uid = uuid.uuid5(uuid.NAMESPACE_URL, "studioflow:user:" + username).hex
    c.execute(
        "INSERT INTO users(id,username) VALUES(?,?) ON CONFLICT(username) DO NOTHING",
        (uid, username),
    )
    return uid


def ensure_product(c, sku):
    sku = sku.strip().upper()
    pid = uuid.uuid5(uuid.NAMESPACE_URL, "studioflow:product:" + sku).hex
    c.execute(
        "INSERT INTO products(id,sku) VALUES(?,?) ON CONFLICT(sku) DO NOTHING",
        (pid, sku),
    )
    return c.execute("SELECT id FROM products WHERE sku=?", (sku,)).fetchone()[0]


def next_sequence(c):
    if getattr(c, "dialect", "sqlite") == "postgresql":
        return c.execute("SELECT nextval('render_enqueue_sequence')").fetchone()[0]
    return c.execute("SELECT COALESCE(MAX(enqueue_seq),0)+1 FROM renders").fetchone()[0]


def link_asset(c, aid, owner_type, owner_id):
    if aid:
        c.execute(
            "INSERT INTO asset_links(asset_id,owner_type,owner_id) VALUES(?,?,?) ON CONFLICT DO NOTHING",
            (aid, owner_type, owner_id),
        )


def link_snapshot(c, snapshot, owner_id):
    for profile in (snapshot.get("branding"), snapshot.get("aplus")):
        if not profile:
            continue
        if profile.get("logo_asset_id"):
            link_asset(c, profile["logo_asset_id"], "snapshot", owner_id)
        elif profile.get("logo_file"):
            mapped = c.execute(
                "SELECT asset_id FROM legacy_assets WHERE path=?",
                ("brand_logos/" + profile["logo_file"],),
            ).fetchone()
            if mapped:
                link_asset(c, mapped[0], "snapshot", owner_id)


def initialize_preview(workspace):
    """Add feature tables to disposable/legacy SQLite previews without importing data."""
    from sqlalchemy import create_engine
    from .schema import metadata, EXTENSIONS

    with workspace.db() as c:
        c.execute("BEGIN IMMEDIATE")
        for table, columns in EXTENSIONS.items():
            present = {r["name"] for r in c.execute(f"PRAGMA table_info({table})")}
            if not present:
                # New feature tables are created below. Existing preview tables
                # need additive upgrades: create_all never adds missing columns.
                continue
            for name, declaration in columns.items():
                if name not in present:
                    c.execute(f"ALTER TABLE {table} ADD COLUMN {name} {declaration}")
        c.execute("UPDATE renders SET enqueue_seq=rowid WHERE enqueue_seq IS NULL")
    preview_engine = create_engine("sqlite:///" + str(workspace.db_path))
    metadata.create_all(preview_engine)
    preview_engine.dispose()
    with workspace.db() as c:
        c.execute(
            "CREATE INDEX IF NOT EXISTS ix_preview_queue ON renders(status,enqueue_seq)"
        )
