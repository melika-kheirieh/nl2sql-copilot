import os
from dotenv import load_dotenv

load_dotenv()


def get_env_var(name: str, required: bool = True, default: str | None = None) -> str | None:
    val = os.getenv(name, default)
    if required and not val:
        raise ValueError(f"Missing required environment variable: {name}")
    return val

proxy_key = os.getenv("PROXY_API_KEY")
proxy_base = os.getenv("PROXY_BASE_URL")
openai_key = os.getenv("OPENAI_API_KEY")
openai_base = os.getenv("OPENAI_BASE_URL")

api_key = proxy_key or openai_key
if not api_key:
    raise ValueError("Missing API key: set PROXY_API_KEY or OPENAI_API_KEY in environment/secrets.")

base_url = proxy_base or openai_base or "https://api.openai.com/v1"

os.environ["OPENAI_API_KEY"] = api_key
os.environ["OPENAI_BASE_URL"] = base_url

MODE = "proxy" if proxy_key else "direct"
OPENAI_API_KEY = api_key
OPENAI_BASE_URL = base_url


LLM_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")  # or gpt-4o, gpt-4o-mini
LLM_TEMPERATURE = float(os.getenv("LLM_TEMPERATURE", "0"))

FORBIDDEN_KEYWORDS = {
    "ATTACH", "PRAGMA",
    "CREATE", "DROP", "ALTER", "VACUUM", "REINDEX", "TRIGGER",
    "INSERT", "UPDATE", "DELETE", "REPLACE",
    "GRANT", "REVOKE",
    "BEGIN", "END", "COMMIT", "ROLLBACK",
    "DETACH",
}
FORBIDDEN_TABLES = {"sqlite_master", "sqlite_temp_master"}
