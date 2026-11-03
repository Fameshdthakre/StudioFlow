"""SQLite connections and additive, versioned schema migrations."""
import sqlite3
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def connect(path):
    con = sqlite3.connect(path, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute('PRAGMA foreign_keys=ON')
    try:
        yield con
        con.commit()
    except BaseException:
        con.rollback()
        raise
    finally:
        con.close()


SCHEMA = """
        CREATE TABLE IF NOT EXISTS jobs (
          id TEXT PRIMARY KEY, user TEXT NOT NULL, kind TEXT NOT NULL, created_at TEXT,
          total INTEGER DEFAULT 0, status TEXT DEFAULT 'running'
        );
        CREATE TABLE IF NOT EXISTS renders (
          id TEXT PRIMARY KEY, job_id TEXT, user TEXT, sku TEXT, kind TEXT,
          source TEXT, original TEXT, prompt TEXT, model TEXT, quality TEXT,
          canvas TEXT, file TEXT, status TEXT DEFAULT 'queued', error TEXT DEFAULT '',
          approved INTEGER DEFAULT 0, parent_id TEXT DEFAULT '', created_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_renders_job ON renders(job_id);
        CREATE TABLE IF NOT EXISTS sessions (
          token TEXT PRIMARY KEY, user TEXT NOT NULL, created_at TEXT, expires_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_sessions_token ON sessions(token);
        CREATE TABLE IF NOT EXISTS memory_settings (
          key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_by TEXT, updated_at TEXT
        );
        CREATE TABLE IF NOT EXISTS product_memory (
          sku TEXT PRIMARY KEY, facts TEXT NOT NULL DEFAULT '', updated_by TEXT, updated_at TEXT
        );
        CREATE TABLE IF NOT EXISTS product_rules (
          id TEXT PRIMARY KEY, sku TEXT NOT NULL, instruction TEXT NOT NULL,
          created_by TEXT, created_at TEXT, source_render_id TEXT DEFAULT ''
        );
        CREATE INDEX IF NOT EXISTS idx_product_rules_sku ON product_rules(sku);
        CREATE TABLE IF NOT EXISTS memory_audit (
          id TEXT PRIMARY KEY, user TEXT, event TEXT, sku TEXT, content TEXT, created_at TEXT
        );
        CREATE TABLE IF NOT EXISTS brands (
          id TEXT PRIMARY KEY, name TEXT NOT NULL COLLATE NOCASE UNIQUE,
          primary_color TEXT NOT NULL DEFAULT '#233A54',
          secondary_color TEXT NOT NULL DEFAULT '#F2EEE8',
          accent_color TEXT NOT NULL DEFAULT '#B98E5C',
          heading_font TEXT NOT NULL DEFAULT 'Cinzel',
          body_font TEXT NOT NULL DEFAULT 'Roboto',
          visual_style TEXT NOT NULL DEFAULT '',
          brand_rules TEXT NOT NULL DEFAULT '',
          logo_file TEXT NOT NULL DEFAULT '',
          updated_by TEXT, updated_at TEXT
        );
        CREATE TABLE IF NOT EXISTS catalog_products (
          id TEXT PRIMARY KEY,
          sku TEXT NOT NULL COLLATE NOCASE,
          asin TEXT DEFAULT '' COLLATE NOCASE,
          item_code TEXT DEFAULT '' COLLATE NOCASE,
          colour TEXT DEFAULT '',
          size TEXT DEFAULT '',
          variation TEXT DEFAULT '',
          title TEXT DEFAULT '',
          item_highlight TEXT DEFAULT '',
          bullet_1 TEXT DEFAULT '',
          bullet_2 TEXT DEFAULT '',
          bullet_3 TEXT DEFAULT '',
          bullet_4 TEXT DEFAULT '',
          bullet_5 TEXT DEFAULT '',
          facts_summary TEXT DEFAULT '',
          brand_id TEXT DEFAULT '',
          updated_by TEXT DEFAULT '',
          updated_at TEXT DEFAULT ''
        );
        CREATE INDEX IF NOT EXISTS idx_cat_sku ON catalog_products(sku);
        CREATE INDEX IF NOT EXISTS idx_cat_asin ON catalog_products(asin);
        CREATE INDEX IF NOT EXISTS idx_cat_item_code ON catalog_products(item_code);
        """


def initialize(path):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with connect(path) as c:
        c.execute('PRAGMA journal_mode=WAL')
        c.executescript(SCHEMA)
        c.execute('CREATE TABLE IF NOT EXISTS schema_migrations(version INTEGER PRIMARY KEY)')
        c.execute('BEGIN IMMEDIATE')
        if not c.execute('SELECT 1 FROM schema_migrations WHERE version=1').fetchone():
            additions = {
                'renders': {'memory_snapshot': "TEXT DEFAULT ''", 'worker_id': "TEXT DEFAULT ''", 'pending_rule': "TEXT DEFAULT ''"},
                'product_memory': {'brand_id': "TEXT DEFAULT ''"},
            }
            for table, columns in additions.items():
                existing = {row['name'] for row in c.execute(f'PRAGMA table_info({table})')}
                for name, declaration in columns.items():
                    if name not in existing:
                        c.execute(f'ALTER TABLE {table} ADD COLUMN {name} {declaration}')
            c.execute('CREATE TABLE IF NOT EXISTS workers(id TEXT PRIMARY KEY, heartbeat REAL NOT NULL)')
            c.execute("CREATE TABLE IF NOT EXISTS idempotency(user TEXT NOT NULL, key TEXT NOT NULL, route TEXT NOT NULL, fingerprint TEXT NOT NULL, response TEXT NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY(user,key))")
            c.execute('CREATE INDEX IF NOT EXISTS idx_renders_status ON renders(status,created_at)')
            c.execute('INSERT INTO schema_migrations(version) VALUES(1)')
        if not c.execute('SELECT 1 FROM schema_migrations WHERE version=2').fetchone():
            duplicates=c.execute('''SELECT 1 FROM catalog_products
                GROUP BY sku COLLATE NOCASE HAVING COUNT(*)>1 LIMIT 1''').fetchone()
            if duplicates:
                import logging
                logging.getLogger(__name__).warning(
                    'Catalog contains duplicate SKUs; unique index deferred. '
                    'Resolve duplicate catalog records to enable uniqueness on next startup.')
            else:
                c.execute('CREATE UNIQUE INDEX IF NOT EXISTS ux_catalog_sku ON catalog_products(sku COLLATE NOCASE)')
                c.execute('INSERT INTO schema_migrations(version) VALUES(2)')
        if not c.execute('SELECT 1 FROM schema_migrations WHERE version=3').fetchone():
            c.execute('''CREATE TABLE IF NOT EXISTS aplus_profiles (
                id TEXT PRIMARY KEY, name TEXT NOT NULL COLLATE NOCASE UNIQUE,
                master_instructions TEXT NOT NULL, branding_notes TEXT NOT NULL DEFAULT '',
                heading_font TEXT NOT NULL DEFAULT 'Cinzel', body_font TEXT NOT NULL DEFAULT 'Roboto',
                primary_color TEXT NOT NULL DEFAULT '#283F44', secondary_color TEXT NOT NULL DEFAULT '#F4E7D9',
                accent_color TEXT NOT NULL DEFAULT '#9E7655', logo_file TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL, updated_by TEXT NOT NULL)''')
            columns = {row['name'] for row in c.execute('PRAGMA table_info(renders)')}
            if 'aplus_profile_id' not in columns:
                c.execute("ALTER TABLE renders ADD COLUMN aplus_profile_id TEXT NOT NULL DEFAULT ''")
            c.execute('INSERT INTO schema_migrations(version) VALUES(3)')
        if not c.execute('SELECT 1 FROM schema_migrations WHERE version=4').fetchone():
            c.execute('''CREATE TABLE usage_ledger (
                id INTEGER PRIMARY KEY AUTOINCREMENT, attempt_id TEXT NOT NULL UNIQUE,
                render_id TEXT NOT NULL REFERENCES renders(id), job_id TEXT NOT NULL,
                user TEXT NOT NULL, model TEXT NOT NULL, kind TEXT NOT NULL,
                request_id TEXT, status TEXT NOT NULL DEFAULT 'started', usage_json TEXT,
                input_tokens INTEGER, image_input_tokens INTEGER, text_input_tokens INTEGER,
                output_tokens INTEGER, image_output_tokens INTEGER, text_output_tokens INTEGER,
                cost_microusd INTEGER CHECK(cost_microusd>=0), meter_status TEXT NOT NULL DEFAULT 'unreported',
                rate_card_version TEXT NOT NULL, rates_json TEXT NOT NULL,
                started_at TEXT NOT NULL, received_at TEXT, finished_at TEXT)''')
            c.execute('CREATE INDEX idx_usage_render ON usage_ledger(render_id)')
            c.execute('CREATE INDEX idx_usage_user ON usage_ledger(user,id)')
            c.execute('''CREATE TABLE usage_budget (
                id INTEGER PRIMARY KEY CHECK(id=1), balance_microusd INTEGER NOT NULL CHECK(balance_microusd>=0),
                ledger_boundary INTEGER NOT NULL, alert_percent INTEGER NOT NULL CHECK(alert_percent BETWEEN 1 AND 90),
                updated_at TEXT NOT NULL, updated_by TEXT NOT NULL)''')
            c.execute('INSERT INTO schema_migrations(version) VALUES(4)')
