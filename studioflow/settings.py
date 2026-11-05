"""Validated runtime configuration, independent of application startup."""

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

MAX_IMAGES = 250
MAX_PENDING_IMAGES = 1000
MAX_IMAGE_BYTES = 18 * 1024 * 1024
MAX_BATCH_BYTES = 100 * 1024 * 1024


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    mode: str
    api_key: str
    max_workers: int
    team: dict[str, str]
    admins: set[str]
    anonymous_demo: bool
    secure_cookie: bool
    enable_aplus: bool = True
    enable_usage: bool = True
    database_url: str = ""
    storage_backend: str = "local"
    s3_endpoint: str = ""
    s3_bucket: str = "studioflow"
    s3_region: str = "us-east-1"
    s3_access_key: str = ""
    s3_secret_key: str = ""
    enable_creation: bool = True
    enable_composition: bool = True
    enable_exports: bool = True
    validated_high_res_models: tuple[str, ...] = ()

    @classmethod
    def load(cls, base: Path):
        if os.getenv("STUDIOFLOW_LOAD_DOTENV", "1") == "1":
            load_dotenv(base / ".env", override=False)
        mode = os.getenv("APP_MODE", "demo").strip().lower()
        if mode not in ("demo", "live"):
            raise RuntimeError("APP_MODE must be demo or live")
        team = {}
        for entry in os.getenv("TEAM_USERS", "").split(","):
            if not entry.strip():
                continue
            name, separator, password = entry.partition(":")
            if not separator or not name.strip() or not password:
                raise RuntimeError("TEAM_USERS must contain username:password entries")
            team[name.strip()] = password
        anonymous = os.getenv("ALLOW_ANONYMOUS_DEMO", "0") == "1"
        require_auth = os.getenv("STUDIOFLOW_REQUIRE_AUTH", "0") == "1"
        if not team and (mode == "live" or require_auth or not anonymous):
            raise RuntimeError(
                "Configure TEAM_USERS, or explicitly set ALLOW_ANONYMOUS_DEMO=1 for a local demo"
            )
        key = os.getenv("OPENAI_API_KEY", "").strip()
        if mode == "live" and not key:
            raise RuntimeError("Live mode requires OPENAI_API_KEY")
        admins = {
            x.strip() for x in os.getenv("ADMIN_USERS", "").split(",") if x.strip()
        }
        if not admins and team:
            admins = {next(iter(team))}
        database_url = os.getenv("DATABASE_URL", "").strip()
        if database_url and not database_url.startswith("postgresql+psycopg://"):
            raise RuntimeError("DATABASE_URL must use postgresql+psycopg://")
        storage_backend = os.getenv(
            "STUDIOFLOW_STORAGE", "s3" if database_url else "local"
        )
        if storage_backend not in ("local", "s3"):
            raise RuntimeError("STUDIOFLOW_STORAGE must be local or s3")
        if storage_backend == "s3" and not all(
            os.getenv(k)
            for k in ("S3_ENDPOINT", "S3_ACCESS_KEY_ID", "S3_SECRET_ACCESS_KEY")
        ):
            raise RuntimeError("Private S3 storage requires endpoint and credentials")
        return cls(
            Path(os.getenv("STUDIOFLOW_DATA_DIR", str(base / "data"))).resolve(),
            mode,
            key,
            max(1, min(8, int(os.getenv("MAX_WORKERS", "3")))),
            team,
            admins,
            mode == "demo" and anonymous and not team,
            os.getenv("COOKIE_SECURE", "0") == "1",
            os.getenv("STUDIOFLOW_ENABLE_APLUS", "1") == "1",
            os.getenv("STUDIOFLOW_ENABLE_USAGE", "1") == "1",
            database_url,
            storage_backend,
            os.getenv("S3_ENDPOINT", ""),
            os.getenv("S3_BUCKET", "studioflow"),
            os.getenv("S3_REGION", "us-east-1"),
            os.getenv("S3_ACCESS_KEY_ID", ""),
            os.getenv("S3_SECRET_ACCESS_KEY", ""),
            os.getenv("STUDIOFLOW_ENABLE_CREATION", "1") == "1",
            os.getenv("STUDIOFLOW_ENABLE_COMPOSITION", "1") == "1",
            os.getenv("STUDIOFLOW_ENABLE_EXPORTS", "1") == "1",
            tuple(
                filter(None, os.getenv("STUDIOFLOW_VALIDATED_2K_MODELS", "").split(","))
            ),
        )
