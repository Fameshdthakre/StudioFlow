"""Per-application configuration and paths; no process-global runtime state."""

from dataclasses import dataclass
from pathlib import Path
from functools import cached_property

from .database import connect
from .settings import Settings


@dataclass
class Workspace:
    settings: Settings
    base_dir: Path

    @property
    def data(self):
        return self.settings.data_dir

    @property
    def db_path(self):
        return self.data / "studio.sqlite3"

    @property
    def uploads(self):
        return self.data / "uploads"

    @property
    def outputs(self):
        return self.data / "outputs"

    @property
    def brand_logos(self):
        return self.data / "brand_logos"

    def db(self):
        if self.settings.database_url:
            from .persistence.connection import connect as postgres_connect

            return postgres_connect(self.settings.database_url)
        return connect(self.db_path)

    def lease_db(self):
        if self.settings.database_url:
            from .persistence.connection import connect_lease

            return connect_lease(self.settings.database_url)
        return self.db()

    @property
    def staging(self):
        return self.data / "staging"

    @property
    def exports(self):
        return self.data / "exports"

    @cached_property
    def storage(self):
        from .storage import LocalStore, S3Store

        return (
            S3Store(self.settings)
            if self.settings.storage_backend == "s3"
            else LocalStore(self.data / "objects")
        )

    def is_admin(self, user):
        return (
            self.settings.anonymous_demo and user == "Demo user"
        ) or user in self.settings.admins
