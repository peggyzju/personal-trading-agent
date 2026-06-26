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


def get_finnhub_key() -> str:
    """Finnhub API key — 财报日历 / EPS 实际vs预期 / 历史超预期(当前、含本年)。
    比 yfinance 实时,且带盘前/盘后(bmo/amc)。免费版 60次/分钟够用。"""
    return os.environ.get("FINNHUB_API_KEY") or _load().get("FINNHUB_API_KEY", "")


def get_anthropic_client(timeout: float = 60.0, max_retries: int = 2):
    """Anthropic 客户端 — 显式超时 + 有界重试。
    调度任务绝不能在一个卡住的 API 调用上无限挂起:那会占住一个调度器线程,
    ×线程池大小后整个调度器冻结(2026-06-24 实证)。"""
    import anthropic
    return anthropic.Anthropic(api_key=get_anthropic_key(),
                               timeout=timeout, max_retries=max_retries)


def get_alpaca_creds() -> tuple[str, str, str]:
    env = _load()
    api_key    = os.environ.get("ALPACA_API_KEY")    or env.get("ALPACA_API_KEY", "")
    secret_key = os.environ.get("ALPACA_SECRET_KEY") or env.get("ALPACA_SECRET_KEY", "")
    base_url   = os.environ.get("ALPACA_BASE_URL")   or env.get("ALPACA_BASE_URL", "https://paper-api.alpaca.markets")
    return api_key, secret_key, base_url
