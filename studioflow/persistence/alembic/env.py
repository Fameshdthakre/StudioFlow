import os
from alembic import context
from sqlalchemy import create_engine
from studioflow.persistence.schema import metadata

url = context.config.attributes.get("database_url") or os.environ["DATABASE_URL"]
connection = context.config.attributes.get("connection")


def migrate(connection):
    context.configure(connection=connection, target_metadata=metadata)
    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    context.configure(url=url, target_metadata=metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()
elif connection is not None:
    migrate(connection)
else:
    migration_engine = create_engine(url)
    try:
        with migration_engine.connect() as connection:
            migrate(connection)
    finally:
        migration_engine.dispose()
