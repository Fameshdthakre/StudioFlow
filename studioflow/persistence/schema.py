"""Portable relational schema. PostgreSQL is the production runtime.

Legacy text identifiers remain available to API clients, alongside stable product
IDs. Optional references are NULL, never sentinel foreign keys.
"""

from sqlalchemy import (
    BigInteger,
    Column,
    Float,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    Table,
    Text,
    UniqueConstraint,
    CheckConstraint,
    func,
)

metadata = MetaData()
sequence_type = BigInteger().with_variant(Integer, "sqlite")


def text_col(name, default="", nullable=False, **kwargs):
    return Column(name, Text, nullable=nullable, server_default=default, **kwargs)


def reference(name, target, nullable=True):
    return Column(
        name, Text, ForeignKey(target, ondelete="RESTRICT"), nullable=nullable
    )


users = Table(
    "users",
    metadata,
    Column("id", Text, primary_key=True),
    Column("username", Text, nullable=False, unique=True),
)
products = Table(
    "products",
    metadata,
    Column("id", Text, primary_key=True),
    Column("sku", Text, nullable=False, unique=True),
    Column("archived", Integer, nullable=False, server_default="0"),
)
brands = Table(
    "brands",
    metadata,
    Column("id", Text, primary_key=True),
    text_col("name"),
    text_col("primary_color", "#233A54"),
    text_col("secondary_color", "#F2EEE8"),
    text_col("accent_color", "#B98E5C"),
    text_col("heading_font", "Cinzel"),
    text_col("body_font", "Roboto"),
    text_col("visual_style"),
    text_col("brand_rules"),
    text_col("logo_file"),
    text_col("updated_by"),
    text_col("updated_at"),
)
Index("ux_brand_name", func.lower(brands.c.name), unique=True)
catalog_products = Table(
    "catalog_products",
    metadata,
    Column("id", Text, primary_key=True),
    reference("product_id", "products.id"),
    text_col("sku"),
    *(
        text_col(n)
        for n in (
            "asin",
            "item_code",
            "colour",
            "size",
            "variation",
            "title",
            "item_highlight",
            "bullet_1",
            "bullet_2",
            "bullet_3",
            "bullet_4",
            "bullet_5",
            "facts_summary",
        )
    ),
    reference("brand_id", "brands.id"),
    text_col("updated_by"),
    text_col("updated_at"),
    Column("archived", Integer, nullable=False, server_default="0"),
)
Index("ux_catalog_sku", func.upper(catalog_products.c.sku), unique=True)
Index("ix_catalog_asin", func.upper(catalog_products.c.asin))
Index("ix_catalog_item_code", func.upper(catalog_products.c.item_code))
product_memory = Table(
    "product_memory",
    metadata,
    Column("sku", Text, primary_key=True),
    reference("product_id", "products.id"),
    text_col("facts"),
    text_col("updated_by"),
    text_col("updated_at"),
    reference("brand_id", "brands.id"),
)
memory_settings = Table(
    "memory_settings",
    metadata,
    Column("key", Text, primary_key=True),
    text_col("value"),
    text_col("updated_by"),
    text_col("updated_at"),
)
memory_audit = Table(
    "memory_audit",
    metadata,
    Column("id", Text, primary_key=True),
    text_col("user"),
    text_col("event"),
    text_col("sku"),
    text_col("content"),
    text_col("created_at"),
)
sessions = Table(
    "sessions",
    metadata,
    Column("token", Text, primary_key=True),
    reference("user", "users.username", False),
    text_col("created_at"),
    text_col("expires_at"),
)
aplus_profiles = Table(
    "aplus_profiles",
    metadata,
    Column("id", Text, primary_key=True),
    text_col("name"),
    text_col("master_instructions"),
    text_col("branding_notes"),
    text_col("heading_font", "Cinzel"),
    text_col("body_font", "Roboto"),
    text_col("primary_color", "#283F44"),
    text_col("secondary_color", "#F4E7D9"),
    text_col("accent_color", "#9E7655"),
    text_col("logo_file"),
    text_col("updated_at"),
    text_col("updated_by"),
)
Index("ux_aplus_name", func.lower(aplus_profiles.c.name), unique=True)
jobs = Table(
    "jobs",
    metadata,
    Column("id", Text, primary_key=True),
    reference("user", "users.username", False),
    text_col("kind"),
    text_col("created_at"),
    Column("total", Integer, server_default="0"),
    text_col("status", "running"),
)
assets = Table(
    "assets",
    metadata,
    Column("id", Text, primary_key=True),
    text_col("object_key"),
    text_col("role"),
    text_col("state", "pending"),
    text_col("mime"),
    Column("byte_size", BigInteger, nullable=False),
    Column("width", Integer, nullable=False),
    Column("height", Integer, nullable=False),
    text_col("sha256"),
    text_col("created_by"),
    text_col("created_at"),
    text_col("staging_file"),
    text_col("error"),
    text_col("transfer_worker_id"),
    reference("parent_id", "assets.id"),
    UniqueConstraint("object_key"),
)
Index("ix_asset_state", assets.c.state, assets.c.created_at)
legacy_assets = Table(
    "legacy_assets",
    metadata,
    Column("path", Text, primary_key=True),
    reference("asset_id", "assets.id", False),
)
image_sets = Table(
    "image_sets",
    metadata,
    Column("id", Text, primary_key=True),
    reference("product_id", "products.id", False),
    reference("job_id", "jobs.id", False),
    text_col("sku"),
    text_col("asin"),
    text_col("spec_json", "{}"),
    text_col("created_at"),
)
renders = Table(
    "renders",
    metadata,
    Column("id", Text, primary_key=True),
    reference("job_id", "jobs.id", False),
    reference("user", "users.username", False),
    text_col("sku"),
    text_col("kind"),
    text_col("source"),
    text_col("original"),
    text_col("prompt"),
    text_col("model"),
    text_col("quality"),
    text_col("canvas", "square"),
    text_col("file"),
    text_col("status", "queued"),
    text_col("error"),
    Column("approved", Integer, nullable=False, server_default="0"),
    reference("parent_id", "renders.id"),
    text_col("created_at"),
    text_col("memory_snapshot"),
    text_col("worker_id"),
    text_col("pending_rule"),
    reference("aplus_profile_id", "aplus_profiles.id"),
    Column("enqueue_seq", sequence_type, nullable=False, unique=True),
    reference("product_id", "products.id"),
    reference("image_set_id", "image_sets.id"),
    text_col("variant"),
    text_col("sku_snapshot"),
    text_col("asin_snapshot"),
    text_col("operation", "generate"),
    text_col("recipe_json", "{}"),
    text_col("references_json", "[]"),
    text_col("native_size"),
    reference("source_asset_id", "assets.id"),
    reference("master_asset_id", "assets.id"),
    reference("output_asset_id", "assets.id"),
    reference("thumbnail_asset_id", "assets.id"),
    reference("depends_on", "renders.id"),
    text_col("stage", "pending"),
    Column("transfer_after", Float, nullable=False, server_default="0"),
)
Index("ix_render_queue", renders.c.status, renders.c.enqueue_seq)
Index("ix_render_library", renders.c.created_at, renders.c.enqueue_seq)
Index("ix_render_product", renders.c.product_id, renders.c.enqueue_seq)
Index("ix_render_job", renders.c.job_id)
Index("ix_render_parent", renders.c.parent_id)
Index("ix_render_set", renders.c.image_set_id, renders.c.variant)
product_rules = Table(
    "product_rules",
    metadata,
    Column("id", Text, primary_key=True),
    text_col("sku"),
    text_col("instruction"),
    text_col("created_by"),
    text_col("created_at"),
    reference("source_render_id", "renders.id"),
    reference("product_id", "products.id"),
)
Index("ix_rules_product", product_rules.c.product_id)
approvals = Table(
    "approvals",
    metadata,
    Column("id", Text, primary_key=True),
    reference("render_id", "renders.id", False),
    reference("user", "users.username", False),
    Column("approved", Integer, nullable=False),
    text_col("created_at"),
)
Index("ix_approval_render", approvals.c.render_id, approvals.c.created_at)
workers = Table(
    "workers",
    metadata,
    Column("id", Text, primary_key=True),
    Column("heartbeat", Float, nullable=False),
)
idempotency = Table(
    "idempotency",
    metadata,
    Column("user", Text, primary_key=True),
    Column("key", Text, primary_key=True),
    text_col("route"),
    text_col("fingerprint"),
    text_col("response"),
    text_col("created_at"),
)
usage_ledger = Table(
    "usage_ledger",
    metadata,
    Column("id", sequence_type, primary_key=True, autoincrement=True),
    Column("attempt_id", Text, nullable=False, unique=True),
    reference("render_id", "renders.id", False),
    reference("job_id", "jobs.id", False),
    text_col("user"),
    text_col("model"),
    text_col("kind"),
    Column("request_id", Text),
    text_col("status", "started"),
    Column("usage_json", Text),
    *(
        Column(n, BigInteger)
        for n in (
            "input_tokens",
            "image_input_tokens",
            "text_input_tokens",
            "output_tokens",
            "image_output_tokens",
            "text_output_tokens",
            "cost_microusd",
        )
    ),
    text_col("meter_status", "unreported"),
    text_col("rate_card_version"),
    text_col("rates_json"),
    text_col("started_at"),
    Column("received_at", Text),
    Column("finished_at", Text),
    CheckConstraint("cost_microusd >= 0"),
)
Index("ix_usage_render", usage_ledger.c.render_id)
Index("ix_usage_user", usage_ledger.c.user, usage_ledger.c.id)
usage_budget = Table(
    "usage_budget",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("balance_microusd", BigInteger, nullable=False),
    Column("ledger_boundary", BigInteger, nullable=False),
    Column("alert_percent", Integer, nullable=False),
    text_col("updated_at"),
    text_col("updated_by"),
    CheckConstraint(
        "id = 1 AND balance_microusd >= 0 AND alert_percent BETWEEN 1 AND 90"
    ),
)
asset_links = Table(
    "asset_links",
    metadata,
    reference("asset_id", "assets.id", False),
    Column("owner_type", Text, nullable=False),
    Column("owner_id", Text, nullable=False),
    UniqueConstraint("asset_id", "owner_type", "owner_id"),
)
exports = Table(
    "exports",
    metadata,
    Column("id", Text, primary_key=True),
    text_col("user"),
    text_col("status", "queued"),
    text_col("selection_json", "[]"),
    text_col("filename", "studioflow-images.zip"),
    text_col("error"),
    text_col("created_at"),
    Column("expires_at", Float, nullable=False),
    text_col("worker_id"),
)
operations = Table(
    "operations",
    metadata,
    Column("key", Text, primary_key=True),
    text_col("value"),
    text_col("updated_at"),
)

# Additional columns applied to legacy SQLite preview workspaces only. PostgreSQL
# is provisioned by Alembic; application startup never upgrades its schema.
EXTENSIONS = {
    "assets": {"transfer_worker_id": "TEXT NOT NULL DEFAULT ''"},
    "catalog_products": {
        "product_id": "TEXT",
        "archived": "INTEGER NOT NULL DEFAULT 0",
    },
    "product_memory": {"product_id": "TEXT"},
    "product_rules": {"product_id": "TEXT"},
    "renders": {
        "enqueue_seq": "INTEGER",
        "product_id": "TEXT",
        "image_set_id": "TEXT",
        **{
            n: "TEXT NOT NULL DEFAULT ''"
            for n in ("variant", "sku_snapshot", "asin_snapshot", "native_size")
        },
        "operation": "TEXT NOT NULL DEFAULT 'generate'",
        "recipe_json": "TEXT NOT NULL DEFAULT '{}'",
        "references_json": "TEXT NOT NULL DEFAULT '[]'",
        **{
            n: "TEXT"
            for n in (
                "source_asset_id",
                "master_asset_id",
                "output_asset_id",
                "thumbnail_asset_id",
                "depends_on",
            )
        },
        "stage": "TEXT NOT NULL DEFAULT 'pending'",
        "transfer_after": "REAL NOT NULL DEFAULT 0",
    },
}
