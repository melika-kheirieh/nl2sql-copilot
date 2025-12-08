from functools import lru_cache
import os

from app.services.nl2sql_service import NL2SQLService


@lru_cache()
def get_nl2sql_service() -> NL2SQLService:
    """
    Singleton-ish NL2SQLService for the FastAPI app.

    Reads config from env once and reuses the same service instance.
    """
    config_path = os.getenv("PIPELINE_CONFIG", "configs/sqlite_pipeline.yaml")
    db_mode = os.getenv("DB_MODE", "sqlite")
    default_sqlite_path = os.getenv("DEFAULT_SQLITE_PATH", "data/demo.db")

    return NL2SQLService(
        config_path=config_path,
        db_mode=db_mode,
        default_sqlite_path=default_sqlite_path,
    )
