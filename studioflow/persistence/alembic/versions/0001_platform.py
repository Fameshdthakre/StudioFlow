"""Relational records, immutable image assets, and durable work queues."""

from alembic import op
from sqlalchemy import text
from studioflow.persistence.schema import metadata

revision = "0001_platform"
down_revision = None


def upgrade():
    bind = op.get_bind()
    metadata.create_all(bind)
    bind.execute(text("CREATE SEQUENCE IF NOT EXISTS render_enqueue_sequence"))
    bind.execute(
        text(
            "ALTER TABLE renders ALTER COLUMN enqueue_seq SET DEFAULT nextval('render_enqueue_sequence')"
        )
    )


def downgrade():
    raise RuntimeError(
        "Destructive downgrade is disabled; restore into a fresh verified stack."
    )
