"""
Market Regime Detection

Classifies the current market state using SPY data.
Used by TradeAgent to gate buy signals.

Regimes:
  BULL     — trend up, normal conditions → full buy signals allowed
  NEUTRAL  — sideways, mixed → allow buys but require ai_score >= 7
  CAUTION  — SPY dropped >1.5% today OR below MA20 → reduce sizing 50%, require ai_score >= 8
  BEAR     — SPY below MA50 by >2% → block ALL buy signals
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import yfinance as yf

_CACHE_FILE = Path(__file__).parent.parent.parent / "data" / "regime_cache.json"
_CACHE_TTL_SECONDS = 900   # re-check every 15 minutes


def _load_cache() -> dict | None:
    try:
        if _CACHE_FILE.exists():
            data = json.loads(_CACHE_FILE.read_text())
            age = (datetime.now(timezone.utc).timestamp() - data.get("fetched_at", 0))
            if age < _CACHE_TTL_SECONDS:
                return data
    except Exception:
        pass
    return None


def _save_cache(regime: dict):
    try:
        _CACHE_FILE.parent.mkdir(exist_ok=True)
        _CACHE_FILE.write_text(json.dumps(regime))
    except Exception:
        pass


def get_market_regime(force_refresh: bool = False) -> dict:
    """
    Returns a dict with:
      regime        : "BULL" | "NEUTRAL" | "CAUTION" | "BEAR"
      spy_price     : float
      spy_change_pct: float  (today's %)
      spy_vs_ma20   : float  (% above/below 20-day MA)
      spy_vs_ma50   : float  (% above/below 50-day MA)
      min_ai_score  : int    (minimum ai_score to allow buy)
      size_factor   : float  (multiplier for position size: 1.0 = full, 0.5 = half)
      block_buys    : bool   (True = no buy signals allowed)
      reason        : str
      fetched_at    : float  (unix timestamp)
    """
    if not force_refresh:
        cached = _load_cache()
        if cached:
            return cached

    try:
        df = yf.download("SPY", period="60d", interval="1d",
                         auto_adjust=True, progress=False)
        if df.empty or len(df) < 20:
            return _fallback("SPY data unavailable")

        closes = df["Close"].dropna()
        spy_price = float(closes.iloc[-1])
        spy_prev  = float(closes.iloc[-2]) if len(closes) >= 2 else spy_price
        spy_change_pct = (spy_price - spy_prev) / spy_prev * 100

        ma20 = float(closes.rolling(20).mean().iloc[-1])
        ma50 = float(closes.rolling(50).mean().iloc[-1]) if len(closes) >= 50 else ma20
        vs_ma20 = (spy_price - ma20) / ma20 * 100
        vs_ma50 = (spy_price - ma50) / ma50 * 100

        # ── Regime classification ─────────────────────────────────────────────
        if vs_ma50 < -2.0:
            regime = "BEAR"
            block_buys   = True
            size_factor  = 0.0
            min_ai_score = 10   # effectively blocked
            reason = f"SPY is {vs_ma50:.1f}% below MA50 — bear market, all buys blocked"

        elif spy_change_pct < -1.5 or vs_ma20 < -1.0:
            regime = "CAUTION"
            block_buys   = False
            size_factor  = 0.5
            min_ai_score = 8
            reason = (
                f"SPY down {abs(spy_change_pct):.1f}% today" if spy_change_pct < -1.5
                else f"SPY is {vs_ma20:.1f}% below MA20"
            ) + " — half sizing, require score ≥ 8"

        elif vs_ma20 >= 0 and vs_ma50 >= 0 and spy_change_pct > -0.5:
            regime = "BULL"
            block_buys   = False
            size_factor  = 1.0
            min_ai_score = 7
            reason = f"SPY +{vs_ma20:.1f}% vs MA20, +{vs_ma50:.1f}% vs MA50 — full signals"

        else:
            regime = "NEUTRAL"
            block_buys   = False
            size_factor  = 0.75
            min_ai_score = 7
            reason = f"SPY mixed ({vs_ma20:+.1f}% vs MA20) — reduced sizing"

        result = {
            "regime": regime,
            "spy_price": round(spy_price, 2),
            "spy_change_pct": round(spy_change_pct, 2),
            "spy_vs_ma20": round(vs_ma20, 2),
            "spy_vs_ma50": round(vs_ma50, 2),
            "min_ai_score": min_ai_score,
            "size_factor": size_factor,
            "block_buys": block_buys,
            "reason": reason,
            "fetched_at": datetime.now(timezone.utc).timestamp(),
        }
        _save_cache(result)
        return result

    except Exception as e:
        return _fallback(f"Error fetching SPY: {e}")


def _fallback(reason: str) -> dict:
    """Safe default when SPY data is unavailable — don't block trading."""
    return {
        "regime": "NEUTRAL",
        "spy_price": 0,
        "spy_change_pct": 0,
        "spy_vs_ma20": 0,
        "spy_vs_ma50": 0,
        "min_ai_score": 7,
        "size_factor": 1.0,
        "block_buys": False,
        "reason": reason,
        "fetched_at": datetime.now(timezone.utc).timestamp(),
    }
