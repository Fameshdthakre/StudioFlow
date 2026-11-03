"""Original pre-versioned workspace schema."""

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
