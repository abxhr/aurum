"""Tiny .env loader — avoids adding python-dotenv as a dependency."""
import os

_ENV_PATH = os.path.join(os.path.dirname(__file__), "..", ".env")


def _load_env():
    if not os.path.exists(_ENV_PATH):
        return
    with open(_ENV_PATH, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            os.environ.setdefault(key.strip(), val.strip())


_load_env()

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
