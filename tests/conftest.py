import os
import pytest
from dotenv import load_dotenv

from app.main import app
from app.routers import nl2sql

# Load .env once for tests
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENV_PATH = os.path.join(ROOT, ".env")
load_dotenv(ENV_PATH)


# API key
proxy_key = os.getenv("PROXY_API_KEY")
if "OPENAI_API_KEY" not in os.environ:
    if proxy_key is not None:
        os.environ["OPENAI_API_KEY"] = proxy_key
    else:
        os.environ["OPENAI_API_KEY"] = "DUMMY_TEST_KEY"

# Base URL
proxy_base_url = os.getenv("PROXY_BASE_URL")
if "OPENAI_BASE_URL" not in os.environ:
    if proxy_base_url is not None:
        os.environ["OPENAI_BASE_URL"] = proxy_base_url
    else:
        os.environ["OPENAI_BASE_URL"] = "http://localhost:9999"


@pytest.fixture(autouse=True)
def disable_api_key_auth():
    """Disable X-API-Key auth for tests."""
    prev = app.dependency_overrides.get(nl2sql.require_api_key)
    app.dependency_overrides[nl2sql.require_api_key] = lambda: None
    try:
        yield
    finally:
        if prev is None:
            app.dependency_overrides.pop(nl2sql.require_api_key, None)
        else:
            app.dependency_overrides[nl2sql.require_api_key] = prev
