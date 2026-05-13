"""
Sector Concentration Control

Before queuing a buy, checks if adding this symbol would breach
sector concentration limits.

Rules:
  - max 2 positions in any single sector  (MAX_PER_SECTOR)
  - max 40% of portfolio in any single sector  (MAX_SECTOR_PCT)

Sector data is fetched from yfinance and cached to disk for 24 h
to avoid hammering the API on every agent run.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import yfinance as yf

_SECTOR_CACHE_FILE = Path(__file__).parent.parent.parent / "data" / "sector_cache.json"
_SECTOR_CACHE_TTL  = 86_400   # 24 hours

MAX_PER_SECTOR  = 2     # max positions per sector
MAX_SECTOR_PCT  = 40.0  # max % of portfolio in one sector


# ── Sector cache ──────────────────────────────────────────────────────────────

def _load_sector_cache() -> dict[str, dict]:
    """Returns {symbol: {"sector": str, "fetched_at": float}}"""
    try:
        if _SECTOR_CACHE_FILE.exists():
            return json.loads(_SECTOR_CACHE_FILE.read_text())
    except Exception:
        pass
    return {}


def _save_sector_cache(cache: dict):
    try:
        _SECTOR_CACHE_FILE.parent.mkdir(exist_ok=True)
        _SECTOR_CACHE_FILE.write_text(json.dumps(cache))
    except Exception:
        pass


def get_sector(symbol: str) -> str:
    """Return GICS sector string for symbol, or 'Unknown'."""
    cache = _load_sector_cache()
    entry = cache.get(symbol, {})

    if entry and (time.time() - entry.get("fetched_at", 0)) < _SECTOR_CACHE_TTL:
        return entry["sector"]

    try:
        info = yf.Ticker(symbol).info
        sector = info.get("sector") or info.get("sectorKey") or "Unknown"
    except Exception:
        sector = "Unknown"

    cache[symbol] = {"sector": sector, "fetched_at": time.time()}
    _save_sector_cache(cache)
    return sector


def get_sectors_bulk(symbols: list[str]) -> dict[str, str]:
    """Return {symbol: sector} for multiple symbols, using cache where possible."""
    cache = _load_sector_cache()
    now   = time.time()
    result: dict[str, str] = {}
    stale: list[str] = []

    for sym in symbols:
        entry = cache.get(sym, {})
        if entry and (now - entry.get("fetched_at", 0)) < _SECTOR_CACHE_TTL:
            result[sym] = entry["sector"]
        else:
            stale.append(sym)

    # Fetch stale symbols one-by-one (yfinance doesn't batch info well)
    for sym in stale:
        result[sym] = get_sector(sym)

    return result


# ── Concentration check ───────────────────────────────────────────────────────

def check_sector_limit(
    symbol: str,
    positions: list[dict],          # current open positions [{symbol, market_value, ...}]
    portfolio_value: float = 0,
) -> tuple[bool, str]:
    """
    Returns (allowed: bool, reason: str).

    allowed=True  → safe to add
    allowed=False → would breach sector concentration limits
    """
    if not positions:
        return True, ""

    target_sector = get_sector(symbol)
    if target_sector == "Unknown":
        return True, ""   # can't check, allow

    held_symbols = [p["symbol"] for p in positions]
    held_sectors = get_sectors_bulk(held_symbols)

    # Count positions already in target sector
    same_sector = [sym for sym, sec in held_sectors.items() if sec == target_sector]
    if len(same_sector) >= MAX_PER_SECTOR:
        return False, (
            f"{target_sector} already has {len(same_sector)} positions "
            f"({', '.join(same_sector)}) — limit is {MAX_PER_SECTOR}"
        )

    # Check sector weight if portfolio_value is known
    if portfolio_value > 0:
        sector_mv = sum(
            p.get("market_value", 0) for p in positions
            if held_sectors.get(p["symbol"]) == target_sector
        )
        sector_pct = sector_mv / portfolio_value * 100
        if sector_pct >= MAX_SECTOR_PCT:
            return False, (
                f"{target_sector} already {sector_pct:.0f}% of portfolio — "
                f"limit is {MAX_SECTOR_PCT}%"
            )

    return True, ""
