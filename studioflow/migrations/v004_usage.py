"""Existing schema migration 4; version and SQL preserved."""


def apply(c):
    c.execute("""CREATE TABLE usage_ledger (
        id INTEGER PRIMARY KEY AUTOINCREMENT, attempt_id TEXT NOT NULL UNIQUE,
        render_id TEXT NOT NULL REFERENCES renders(id), job_id TEXT NOT NULL,
        user TEXT NOT NULL, model TEXT NOT NULL, kind TEXT NOT NULL,
        request_id TEXT, status TEXT NOT NULL DEFAULT 'started', usage_json TEXT,
        input_tokens INTEGER, image_input_tokens INTEGER, text_input_tokens INTEGER,
        output_tokens INTEGER, image_output_tokens INTEGER, text_output_tokens INTEGER,
        cost_microusd INTEGER CHECK(cost_microusd>=0), meter_status TEXT NOT NULL DEFAULT 'unreported',
        rate_card_version TEXT NOT NULL, rates_json TEXT NOT NULL,
        started_at TEXT NOT NULL, received_at TEXT, finished_at TEXT)""")
    c.execute("CREATE INDEX idx_usage_render ON usage_ledger(render_id)")
    c.execute("CREATE INDEX idx_usage_user ON usage_ledger(user,id)")
    c.execute("""CREATE TABLE usage_budget (
        id INTEGER PRIMARY KEY CHECK(id=1), balance_microusd INTEGER NOT NULL CHECK(balance_microusd>=0),
        ledger_boundary INTEGER NOT NULL, alert_percent INTEGER NOT NULL CHECK(alert_percent BETWEEN 1 AND 90),
        updated_at TEXT NOT NULL, updated_by TEXT NOT NULL)""")
    return True
