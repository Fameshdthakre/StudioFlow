"""Existing schema migration 3; version and SQL preserved."""


def apply(c):
    c.execute("""CREATE TABLE IF NOT EXISTS aplus_profiles (
        id TEXT PRIMARY KEY, name TEXT NOT NULL COLLATE NOCASE UNIQUE,
        master_instructions TEXT NOT NULL, branding_notes TEXT NOT NULL DEFAULT '',
        heading_font TEXT NOT NULL DEFAULT 'Cinzel', body_font TEXT NOT NULL DEFAULT 'Roboto',
        primary_color TEXT NOT NULL DEFAULT '#283F44', secondary_color TEXT NOT NULL DEFAULT '#F4E7D9',
        accent_color TEXT NOT NULL DEFAULT '#9E7655', logo_file TEXT NOT NULL DEFAULT '',
        updated_at TEXT NOT NULL, updated_by TEXT NOT NULL)""")
    columns = {row["name"] for row in c.execute("PRAGMA table_info(renders)")}
    if "aplus_profile_id" not in columns:
        c.execute(
            "ALTER TABLE renders ADD COLUMN aplus_profile_id TEXT NOT NULL DEFAULT ''"
        )
    return True
