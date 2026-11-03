"""Per-application configuration and paths; no process-global runtime state."""

from dataclasses import dataclass
from pathlib import Path

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
        return connect(self.db_path)

    def is_admin(self, user):
        return (
            self.settings.anonymous_demo and user == "Demo user"
        ) or user in self.settings.admins
