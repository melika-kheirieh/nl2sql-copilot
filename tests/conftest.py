import os
from dotenv import load_dotenv

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENV_PATH = os.path.join(ROOT_DIR, ".env")
load_dotenv(dotenv_path=ENV_PATH)


# --- add: make tests independent of real OPENAI_* in CI ---
def _ensure_openai_env_for_tests():
    # map PROXY_* -> OPENAI_* or set a harmless dummy
    if not os.getenv("OPENAI_API_KEY"):
        os.environ["OPENAI_API_KEY"] = os.getenv("PROXY_API_KEY", "DUMMY_TEST_KEY")
    if not os.getenv("OPENAI_BASE_URL") and os.getenv("PROXY_BASE_URL"):
        os.environ["OPENAI_BASE_URL"] = os.environ["PROXY_BASE_URL"]


_ensure_openai_env_for_tests()
