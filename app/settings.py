from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache


@dataclass
class Settings:
    """
    Centralized application configuration.

    This version does NOT depend on pydantic or pydantic-settings.
    Values are loaded from environment variables via Settings.from_env().
    """

    # --- DB mode / adapters ---
    db_mode: str = "sqlite"  # "sqlite" or "postgres"
    postgres_dsn: str = ""

    # --- Pipeline config ---
    pipeline_config_path: str = "configs/sqlite_pipeline.yaml"

    # --- SQLite uploaded DBs ---
    db_upload_dir: str = "/tmp/nl2sql_dbs"
    db_ttl_seconds: int = 7200  # 2 hours

    # --- Upload constraints ---
    upload_max_bytes: int = 20 * 1024 * 1024  # 20MB

    # --- Cache settings ---
    cache_ttl_sec: int = 300
    cache_max_entries: int = 256

    # --- Default SQLite path ---
    default_sqlite_path: str = "data/demo.db"

    # --- API keys (comma-separated) ---
    api_keys_raw: str = ""

    # --- App version ---
    app_version: str = "dev"

    @classmethod
    def from_env(cls) -> "Settings":
        """
        Build Settings from environment variables with sane fallbacks.

        This keeps all env parsing in one place and avoids scattered os.getenv().
        """

        def getenv_int(name: str, default: int) -> int:
            raw = os.getenv(name)
            if raw is None or raw.strip() == "":
                return default
            try:
                return int(raw)
            except ValueError:
                return default

        return cls(
            db_mode=os.getenv("DB_MODE", cls.db_mode),
            postgres_dsn=os.getenv("POSTGRES_DSN", cls.postgres_dsn),
            pipeline_config_path=os.getenv("PIPELINE_CONFIG", cls.pipeline_config_path),
            db_upload_dir=os.getenv("DB_UPLOAD_DIR", cls.db_upload_dir),
            db_ttl_seconds=getenv_int("DB_TTL_SECONDS", cls.db_ttl_seconds),
            upload_max_bytes=getenv_int("UPLOAD_MAX_BYTES", cls.upload_max_bytes),
            cache_ttl_sec=getenv_int("NL2SQL_CACHE_TTL_SEC", cls.cache_ttl_sec),
            cache_max_entries=getenv_int("NL2SQL_CACHE_MAX", cls.cache_max_entries),
            default_sqlite_path=os.getenv(
                "DEFAULT_SQLITE_PATH", cls.default_sqlite_path
            ),
            api_keys_raw=os.getenv("API_KEYS", cls.api_keys_raw),
            app_version=os.getenv("APP_VERSION", cls.app_version),
        )


@lru_cache()
def get_settings() -> Settings:
    """
    Cached Settings instance.

    - Loads from env only once per process.
    - Plays nicely with FastAPI dependency injection.
    """
    return Settings.from_env()
