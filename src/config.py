from __future__ import annotations
import os
from pathlib import Path
from dotenv import dotenv_values

_ENV_PATH = Path(__file__).parent.parent / ".env"


def _load() -> dict[str, str]:
    """Read .env file directly — works regardless of working directory."""
    vals = dotenv_values(_ENV_PATH)
    return {k: v for k, v in vals.items() if v}


def get_anthropic_key() -> str:
    key = os.environ.get("ANTHROPIC_API_KEY") or _load().get("ANTHROPIC_API_KEY", "")
    if not key:
        raise RuntimeError("ANTHROPIC_API_KEY not set. Add it to .env")
    return key


def get_alpaca_creds() -> tuple[str, str, str]:
    env = _load()
    api_key    = os.environ.get("ALPACA_API_KEY")    or env.get("ALPACA_API_KEY", "")
    secret_key = os.environ.get("ALPACA_SECRET_KEY") or env.get("ALPACA_SECRET_KEY", "")
    base_url   = os.environ.get("ALPACA_BASE_URL")   or env.get("ALPACA_BASE_URL", "https://paper-api.alpaca.markets")
    return api_key, secret_key, base_url
