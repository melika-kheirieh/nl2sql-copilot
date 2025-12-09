from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

# Resolve repo root from this file's location:
# app/settings.py → parent = app/ → parent = repo root
REPO_ROOT = Path(__file__).resolve().parents[1]

# Canonical demo DB and pipeline config shipped with the repo
DEFAULT_DEMO_DB = REPO_ROOT / "data" / "demo.db"
DEFAULT_PIPELINE_CONFIG = REPO_ROOT / "configs" / "sqlite_pipeline.yaml"


@dataclass
class Settings:
    """
    Centralized application configuration.

    Does NOT depend on pydantic. Values are loaded from environment
    variables via Settings.from_env().
    """

    # --- DB mode / adapters ---
    db_mode: str = "sqlite"  # "sqlite" or "postgres"
    postgres_dsn: str = ""

    # --- Pipeline config ---
    pipeline_config_path: str = str(DEFAULT_PIPELINE_CONFIG)

    # --- SQLite uploaded DBs ---
    db_upload_dir: str = "/tmp/nl2sql_dbs"
    db_ttl_seconds: int = 7200  # 2 hours

    # --- Upload constraints ---
    upload_max_bytes: int = 20 * 1024 * 1024  # 20MB

    # --- Cache settings ---
    cache_ttl_sec: int = 300
    cache_max_entries: int = 256

    # --- Default SQLite path (demo DB) ---
    default_sqlite_path: str = str(DEFAULT_DEMO_DB)

    # --- API keys (comma-separated) ---
    api_keys_raw: str = ""

    # --- App version ---
    app_version: str = "dev"

    @classmethod
    def from_env(cls) -> "Settings":
        """
        Build Settings from environment variables with sane fallbacks.

        - DEFAULT_SQLITE_PATH and PIPELINE_CONFIG can be absolute or relative.
        - Relative paths are resolved against REPO_ROOT.
        """

        def getenv_int(name: str, default: int) -> int:
            raw = os.getenv(name)
            if raw is None or raw.strip() == "":
                return default
            try:
                return int(raw)
            except ValueError:
                return default

        # --- Default SQLite path ---
        raw_default_db = os.getenv("DEFAULT_SQLITE_PATH", "").strip()
        if raw_default_db:
            db_candidate = Path(raw_default_db)
            if not db_candidate.is_absolute():
                db_candidate = REPO_ROOT / raw_default_db
        else:
            db_candidate = DEFAULT_DEMO_DB

        # --- Pipeline config path ---
        raw_cfg = os.getenv("PIPELINE_CONFIG", "").strip()
        if raw_cfg:
            cfg_candidate = Path(raw_cfg)
            if not cfg_candidate.is_absolute():
                cfg_candidate = REPO_ROOT / raw_cfg
        else:
            cfg_candidate = DEFAULT_PIPELINE_CONFIG

        return cls(
            db_mode=os.getenv("DB_MODE", cls.db_mode),
            postgres_dsn=os.getenv("POSTGRES_DSN", cls.postgres_dsn),
            pipeline_config_path=str(cfg_candidate),
            db_upload_dir=os.getenv("DB_UPLOAD_DIR", cls.db_upload_dir),
            db_ttl_seconds=getenv_int("DB_TTL_SECONDS", cls.db_ttl_seconds),
            upload_max_bytes=getenv_int("UPLOAD_MAX_BYTES", cls.upload_max_bytes),
            cache_ttl_sec=getenv_int("NL2SQL_CACHE_TTL_SEC", cls.cache_ttl_sec),
            cache_max_entries=getenv_int("NL2SQL_CACHE_MAX", cls.cache_max_entries),
            default_sqlite_path=str(db_candidate),
            api_keys_raw=os.getenv("API_KEYS", cls.api_keys_raw),
            app_version=os.getenv("APP_VERSION", cls.app_version),
        )


@lru_cache()
def get_settings() -> Settings:
    return Settings.from_env()
