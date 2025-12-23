from functools import lru_cache

from app.services.nl2sql_service import NL2SQLService
from app.cache import NL2SQLCache
from app.settings import get_settings


@lru_cache()
def get_nl2sql_service() -> NL2SQLService:
    """
    Singleton-ish NL2SQLService for the FastAPI app.

    Uses centralized Settings so configuration is loaded once and injected.
    """
    settings = get_settings()
    return NL2SQLService(settings=settings)


@lru_cache()
def get_cache() -> NL2SQLCache:
    """
    Singleton in-memory cache for NL2SQL responses.

    TTL is loaded from Settings (NL2SQL_CACHE_TTL_SEC).
    """
    settings = get_settings()
    return NL2SQLCache(ttl=float(settings.cache_ttl_sec))
