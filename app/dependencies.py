from functools import lru_cache

from app.services.nl2sql_service import NL2SQLService
from app.settings import get_settings


@lru_cache()
def get_nl2sql_service() -> NL2SQLService:
    """
    Singleton-ish NL2SQLService for the FastAPI app.

    Uses centralized Settings so configuration is loaded once and injected.
    """
    settings = get_settings()
    return NL2SQLService(settings=settings)
