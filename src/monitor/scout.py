from __future__ import annotations
"""
Scout: dynamic ticker discovery from volume anomalies + momentum.

Runs pre-market (8:45 AM ET) to discover stocks *outside* the main S&P 500 /
Nasdaq-100 / Layer2 universe that are showing unusual activity today.

Results are cached in data/dynamic_tickers.json (TTL: same trading day).
`get_scan_universe()` in sp500_scanner.py merges these in automatically.
"""

import json
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

ET = timezone(timedelta(hours=-4))

_CACHE_FILE = Path(__file__).parent.parent.parent / "data" / "dynamic_tickers.json"
MAX_TICKERS  = 30
MIN_PRICE    = 5.0
MAX_PRICE    = 500.0


# ── Cache helpers ─────────────────────────────────────────────────────────────

def _load_cache() -> dict:
    try:
        return json.loads(_CACHE_FILE.read_text())
    except Exception:
        return {}


def _save_cache(data: dict):
    try:
        _CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        _CACHE_FILE.write_text(json.dumps(data, indent=2))
    except Exception as e:
        print(f"[scout] cache save error: {e}")


def get_dynamic_tickers() -> list[str]:
    """Return today's dynamic tickers from cache (empty list if stale or missing)."""
    cache = _load_cache()
    today = datetime.now(ET).strftime("%Y-%m-%d")
    if cache.get("date") == today and cache.get("tickers"):
        return list(cache["tickers"])
    return []


# ── Data source: Finviz screener ──────────────────────────────────────────────

def _fetch_finviz(url: str, label: str) -> list[str]:
    """Scrape ticker symbols from a Finviz screener URL."""
    import urllib.request

    headers = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=15) as resp:
            html = resp.read().decode("utf-8", errors="ignore")
        tickers = re.findall(r'quote\.ashx\?t=([A-Z]{1,5})\b', html)
        result  = list(dict.fromkeys(tickers))[:50]   # dedup, preserve order
        print(f"[scout] {label}: {len(result)} candidates")
        return result
    except Exception as e:
        print(f"[scout] {label} error: {e}")
        return []


def _fetch_finviz_movers() -> list[str]:
    """Stocks up ≥5 % on avg volume > 500 k, price $5–$500, sorted by volume."""
    return _fetch_finviz(
        "https://finviz.com/screener.ashx"
        "?v=111&f=sh_avgvol_o500,sh_price_5to500,ta_change_u5"
        "&o=-volume&r=1",
        "finviz_movers",
    )


def _fetch_finviz_volume_surge() -> list[str]:
    """Stocks with relative volume ≥ 3 × avg, avg volume > 500 k, price $5–$500."""
    return _fetch_finviz(
        "https://finviz.com/screener.ashx"
        "?v=111&f=sh_avgvol_o500,sh_price_5to500,sh_relvol_o3"
        "&o=-relativevolume&r=1",
        "finviz_volume_surge",
    )


# ── Validation ────────────────────────────────────────────────────────────────

def _validate_tickers(candidates: list[str], existing: set[str]) -> list[str]:
    """
    Remove tickers already in the main universe, then spot-check price range
    via yfinance fast_info (light-weight, no history download).
    """
    import yfinance as yf

    novel = [t for t in candidates if t not in existing]
    if not novel:
        print("[scout] All candidates already covered by main universe")
        return []

    print(f"[scout] Validating {len(novel)} novel candidates…")

    def _check(sym: str) -> Optional[str]:
        try:
            fi    = yf.Ticker(sym).fast_info
            price = getattr(fi, "last_price", None) or getattr(fi, "regularMarketPrice", None)
            if price is None:
                return None
            return sym if MIN_PRICE <= float(price) <= MAX_PRICE else None
        except Exception:
            return None

    with ThreadPoolExecutor(max_workers=10) as pool:
        results = list(pool.map(_check, novel[:60]))

    validated = [r for r in results if r]
    return validated[:MAX_TICKERS]


# ── Main entry point ──────────────────────────────────────────────────────────

def run(existing_universe: Optional[set[str]] = None) -> list[str]:
    """
    Discover dynamic tickers, validate, and save to cache.

    Args:
        existing_universe: set of tickers already covered by sp500/ndq100/layer2.
                           If None, loaded automatically from sp500_scanner.

    Returns:
        List of newly discovered tickers (≤ MAX_TICKERS).
    """
    today = datetime.now(ET).strftime("%Y-%m-%d")
    print(f"[scout] Dynamic discovery for {today}…")

    # Cache hit: skip if already ran today
    cache = _load_cache()
    if cache.get("date") == today and cache.get("tickers"):
        tickers = cache["tickers"]
        print(f"[scout] Cache hit — {len(tickers)} tickers already discovered today: {tickers}")
        return tickers

    # Build existing universe if not provided
    if existing_universe is None:
        try:
            from src.monitor.sp500_scanner import (
                get_sp500_tickers, get_nasdaq100_tickers, LAYER2_TICKERS,
            )
            existing_universe = set(
                get_sp500_tickers() + get_nasdaq100_tickers() + LAYER2_TICKERS
            )
            print(f"[scout] Existing universe: {len(existing_universe)} tickers")
        except Exception as e:
            print(f"[scout] Could not load existing universe: {e}")
            existing_universe = set()

    # Fetch from sources
    movers     = _fetch_finviz_movers()
    time.sleep(2)                          # polite delay between Finviz requests
    vol_surge  = _fetch_finviz_volume_surge()

    merged = list(dict.fromkeys(movers + vol_surge))
    print(f"[scout] Merged candidates: {len(merged)}")

    # Validate
    validated = _validate_tickers(merged, existing_universe)

    # Persist
    result = {
        "date":         today,
        "tickers":      validated,
        "sources":      {
            "finviz_movers":       len(movers),
            "finviz_volume_surge": len(vol_surge),
        },
        "generated_at": datetime.utcnow().isoformat() + "Z",
    }
    _save_cache(result)

    print(f"[scout] Done — {len(validated)} dynamic tickers: {validated}")
    return validated
