import os
import pytest
from dotenv import load_dotenv
from app.main import app
from app.routers import nl2sql

# Load .env once for tests
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENV_PATH = os.path.join(ROOT, ".env")
load_dotenv(ENV_PATH)

# --- Ensure fake OpenAI creds for CI/tests ---
if not os.getenv("OPENAI_API_KEY"):
    os.environ["OPENAI_API_KEY"] = os.getenv("PROXY_API_KEY", "DUMMY_TEST_KEY")
if not os.getenv("OPENAI_BASE_URL") and os.getenv("PROXY_BASE_URL"):
    os.environ["OPENAI_BASE_URL"] = os.getenv("PROXY_BASE_URL", "DUMMY_TEST_KEY")


@pytest.fixture(autouse=True)
def disable_api_key_auth():
    """Disable X-API-Key auth for tests."""
    app.dependency_overrides[nl2sql.require_api_key] = lambda: None
    yield
    app.dependency_overrides.clear()
