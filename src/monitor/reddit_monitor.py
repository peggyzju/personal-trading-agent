"""
Reddit / WSB Sentiment Monitor
================================
Fetches WSB (WallStreetBets) mention data from Apewisdom — free API, no key required.

Endpoint: https://apewisdom.io/api/v1.0/filter/all-stocks/page/{n}
Each page returns 25 tickers sorted by 24h mention count.

hype_label classification:
  "none"     — < 10 mentions or not in top 250
  "moderate" — 10–99 mentions, delta < 150%   (interest building, not peaked)
  "high"     — 100–499 mentions, or delta ≥ 150% but < 300%
  "extreme"  — ≥ 500 mentions, or delta ≥ 300% (retail frenzy — top-risk signal)

Usage:
  from src.monitor.reddit_monitor import fetch_wsb_mentions
  wsb_map = fetch_wsb_mentions(["MSTR", "HOOD", "AFRM", ...])
  # wsb_map["MSTR"] = {mentions, mentions_24h_ago, rank, rank_24h_ago, hype_delta, hype_label}
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from urllib.request import urlopen, Request
from urllib.error import URLError

_CACHE_FILE   = Path(__file__).parent.parent.parent / "data" / "wsb_cache.json"
_CACHE_TTL    = 3600        # 1 hour — Apewisdom updates hourly
_PAGES        = 10          # 10 × 25 = 250 tickers, covers all Layer 2
_BASE_URL     = "https://apewisdom.io/api/v1.0/filter/all-stocks/page/{page}"
_TIMEOUT_SECS = 8


def _load_cache() -> dict | None:
    try:
        if _CACHE_FILE.exists():
            data = json.loads(_CACHE_FILE.read_text())
            age  = time.time() - data.get("fetched_at", 0)
            if age < _CACHE_TTL:
                return data.get("symbols", {})
    except Exception:
        pass
    return None


def _save_cache(symbols: dict):
    try:
        _CACHE_FILE.parent.mkdir(exist_ok=True)
        _CACHE_FILE.write_text(json.dumps({
            "fetched_at": time.time(),
            "symbols": symbols,
        }))
    except Exception:
        pass


def _hype_label(mentions: int, delta_pct: float) -> str:
    if mentions < 10:
        return "none"
    if mentions >= 500 or delta_pct >= 300:
        return "extreme"
    if mentions >= 100 or delta_pct >= 150:
        return "high"
    return "moderate"


def _fetch_all_pages() -> dict[str, dict]:
    """Pull up to _PAGES pages from Apewisdom. Returns raw data keyed by ticker."""
    raw: dict[str, dict] = {}
    for page in range(1, _PAGES + 1):
        url = _BASE_URL.format(page=page)
        try:
            req  = Request(url, headers={"User-Agent": "trading-agent/1.0"})
            resp = urlopen(req, timeout=_TIMEOUT_SECS)
            data = json.loads(resp.read().decode())
            results = data.get("results", [])
            if not results:
                break
            for item in results:
                ticker = (item.get("ticker") or "").upper()
                if not ticker:
                    continue
                raw[ticker] = item
        except (URLError, Exception) as e:
            print(f"[wsb] page {page} fetch error: {e}")
            break
    return raw


def fetch_wsb_mentions(
    symbols: list[str],
    force_refresh: bool = False,
) -> dict[str, dict]:
    """
    Returns WSB mention data for the given symbols.
    Missing symbols get hype_label="none".

    Return schema per symbol:
      mentions         : int   — 24h mention count
      mentions_24h_ago : int
      rank             : int   — current rank (lower = more discussed)
      rank_24h_ago     : int
      hype_delta       : float — % change in mentions vs 24h ago
      hype_label       : str   — "none" | "moderate" | "high" | "extreme"
    """
    if not force_refresh:
        cached = _load_cache()
        if cached is not None:
            return _filter_symbols(cached, symbols)

    print("[wsb] Fetching WSB mention data from Apewisdom…")
    raw = _fetch_all_pages()

    processed: dict[str, dict] = {}
    for ticker, item in raw.items():
        mentions      = int(item.get("mentions", 0) or 0)
        mentions_prev = int(item.get("mentions_24h_ago", 0) or 0)
        rank          = int(item.get("rank", 999) or 999)
        rank_prev     = int(item.get("rank_24h_ago", 999) or 999)
        delta         = (mentions - mentions_prev) / max(mentions_prev, 1) * 100
        processed[ticker] = {
            "mentions":         mentions,
            "mentions_24h_ago": mentions_prev,
            "rank":             rank,
            "rank_24h_ago":     rank_prev,
            "hype_delta":       round(delta, 1),
            "hype_label":       _hype_label(mentions, delta),
        }

    _save_cache(processed)
    print(f"[wsb] Loaded {len(processed)} tickers. "
          f"Extreme: {sum(1 for v in processed.values() if v['hype_label']=='extreme')} | "
          f"High: {sum(1 for v in processed.values() if v['hype_label']=='high')}")

    return _filter_symbols(processed, symbols)


def _filter_symbols(data: dict[str, dict], symbols: list[str]) -> dict[str, dict]:
    _none = {"mentions": 0, "mentions_24h_ago": 0, "rank": 999,
             "rank_24h_ago": 999, "hype_delta": 0.0, "hype_label": "none"}
    return {sym: data.get(sym.upper(), _none) for sym in symbols}
