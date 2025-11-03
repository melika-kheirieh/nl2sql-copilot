"""App bootstrap: load .env and prepare environment paths."""

import os
from pathlib import Path

# Change current dir to project root (so relative paths like data/ work)
ROOT_DIR = Path(__file__).resolve().parent.parent
os.chdir(ROOT_DIR)

# Load .env if available
try:
    from dotenv import load_dotenv

    load_dotenv()
    print("✅ bootstrap: .env loaded")
except Exception as e:
    print(f"⚠️ bootstrap: could not load .env ({e})")
