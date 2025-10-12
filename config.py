import os
from dotenv import load_dotenv

# ----------------------------
# Load .env
# ----------------------------
load_dotenv()


def get_env_var(name: str, required: bool = True, default: str | None = None) -> str | None:
    """Safely get an environment variable or raise a clear error if missing."""
    value = os.getenv(name, default)
    if required and not value:
        raise ValueError(f"Missing required environment variable: {name}")
    return value

# ----------------------------
# Detect which mode we're in
# ----------------------------
PROXY_TOKEN = os.getenv("PROXY_API_KEY")
PROXY_BASE_URL = os.getenv("PROXY_BASE_URL")

if PROXY_TOKEN and PROXY_BASE_URL:
    MODE = "proxy"
    os.environ["OPENAI_API_KEY"] = PROXY_TOKEN
    os.environ["OPENAI_BASE_URL"] = PROXY_BASE_URL
else:
    MODE = "direct"
    os.environ["OPENAI_API_KEY"] = get_env_var("OPENAI_API_KEY")
    if base_url := os.getenv("OPENAI_BASE_URL"):
        os.environ["OPENAI_BASE_URL"] = base_url

# ----------------------------
# Exported values
# ----------------------------
OPENAI_API_KEY = os.environ["OPENAI_API_KEY"]
OPENAI_BASE_URL = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1")

# ----------------------------
# Optional logging for clarity
# ----------------------------
print(f"[config] Mode: {MODE.upper()} | Base URL: {OPENAI_BASE_URL}")

LLM_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")  # or gpt-4o, gpt-4o-mini
LLM_TEMPERATURE = float(os.getenv("LLM_TEMPERATURE", "0"))

# Hard blocks (defense-in-depth)
FORBIDDEN_KEYWORDS = {
    "ATTACH", "PRAGMA",
    "CREATE", "DROP", "ALTER", "VACUUM", "REINDEX", "TRIGGER",
    "INSERT", "UPDATE", "DELETE", "REPLACE",
    "GRANT", "REVOKE",
    "BEGIN", "END", "COMMIT", "ROLLBACK",
    "DETACH",
}
FORBIDDEN_TABLES = {"sqlite_master", "sqlite_temp_master"}
