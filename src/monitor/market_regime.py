"""
Market Regime Detection

Classifies the current market state using SPY data.
Used by TradeAgent to gate buy signals.

Regimes (priority order):
  BULL     — SPY above MA5, MA20, MA50 → full signals, max 10 positions
  NEUTRAL  — SPY above MA20 but mixed signals → reduced sizing, max 7 positions
  CAUTION  — SPY below MA5 OR intraday drop >1.5% → half sizing, max 5 positions
  BEAR     — SPY below MA20 → block ALL new buys, max 3 positions (stop management only)
  CRASH    — SPY below MA50 by >2% → block ALL buys, 0 new positions
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import yfinance as yf

_CACHE_FILE = Path(__file__).parent.parent.parent / "data" / "regime_cache.json"
_CACHE_TTL_SECONDS  = 900  # re-check every 15 minutes
_UPGRADE_MIN_COUNT  = 2    # regime improvement needs N consecutive confirmations (~30 min)

# Severity order: higher = worse market conditions
_REGIME_SEVERITY = {"BULL": 0, "NEUTRAL": 1, "CAUTION": 2, "BEAR": 3, "CRASH": 4}


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


def _load_cache_raw() -> dict | None:
    """Read cache regardless of age — used to retrieve pending upgrade state."""
    try:
        if _CACHE_FILE.exists():
            return json.loads(_CACHE_FILE.read_text())
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
      regime        : "BULL" | "NEUTRAL" | "CAUTION" | "BEAR" | "CRASH"
      spy_price     : float
      spy_change_pct: float  (today's %)
      spy_vs_ma5    : float  (% above/below 5-day MA)
      spy_vs_ma20   : float  (% above/below 20-day MA)
      spy_vs_ma50   : float  (% above/below 50-day MA)
      min_ai_score  : int    (minimum ai_score to allow buy)
      size_factor   : float  (multiplier for position size: 1.0 = full, 0.5 = half)
      max_positions : int    (hard cap on concurrent open positions)
      block_buys    : bool   (True = no new buy orders allowed)
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
        if isinstance(closes, pd.DataFrame):
            closes = closes.iloc[:, 0]
        spy_price = float(closes.iloc[-1])
        spy_prev  = float(closes.iloc[-2]) if len(closes) >= 2 else spy_price
        spy_change_pct = (spy_price - spy_prev) / spy_prev * 100

        ma5  = float(closes.rolling(5).mean().iloc[-1])  if len(closes) >= 5  else spy_price
        ma20 = float(closes.rolling(20).mean().iloc[-1]) if len(closes) >= 20 else spy_price
        ma50 = float(closes.rolling(50).mean().iloc[-1]) if len(closes) >= 50 else ma20
        vs_ma5  = (spy_price - ma5)  / ma5  * 100
        vs_ma20 = (spy_price - ma20) / ma20 * 100
        vs_ma50 = (spy_price - ma50) / ma50 * 100

        # ── Regime classification (priority: worst first) ─────────────────────
        if vs_ma50 < -2.0:
            # Full crash — SPY broke down through MA50
            regime        = "CRASH"
            block_buys    = True
            size_factor   = 0.0
            min_ai_score  = 10    # effectively blocked
            max_positions = 0     # no new positions; manage existing stops only
            reason = f"SPY {vs_ma50:.1f}% below MA50 — crash mode, all buys blocked"

        elif vs_ma20 < 0 and vs_ma50 < 0:
            # Below BOTH MA20 and MA50 — mid-term trend genuinely broken, stop new buys.
            # (A1) 只破 MA20、MA50 仍在 = 上升趋势里的回调，不算熊 → 落到下面 CAUTION（减档但不封锁）。
            regime        = "BEAR"
            block_buys    = True
            size_factor   = 0.0
            min_ai_score  = 10
            max_positions = 3     # keep existing positions for stop management
            reason = f"SPY {vs_ma20:.1f}% below MA20 & {vs_ma50:.1f}% below MA50 — new buys blocked, manage stops only"

        elif vs_ma5 < 0 or spy_change_pct < -1.5:
            # Below 5-day MA or sharp intraday drop — yellow alert
            regime        = "CAUTION"
            block_buys    = False
            size_factor   = 0.5
            min_ai_score  = 8
            max_positions = 5     # compress from 10 → 5
            reason = (
                f"SPY {vs_ma5:.1f}% below MA5" if vs_ma5 < 0
                else f"SPY down {abs(spy_change_pct):.1f}% today"
            ) + " — half sizing, max 5 positions, score ≥ 8"

        elif vs_ma20 >= 0 and vs_ma50 >= 0 and spy_change_pct > -0.5:
            # Clean uptrend
            regime        = "BULL"
            block_buys    = False
            size_factor   = 1.0
            min_ai_score  = 7
            max_positions = 10
            reason = f"SPY +{vs_ma20:.1f}% vs MA20, +{vs_ma5:.1f}% vs MA5 — full signals"

        else:
            # Mixed / sideways
            regime        = "NEUTRAL"
            block_buys    = False
            size_factor   = 0.75
            min_ai_score  = 7
            max_positions = 7
            reason = f"SPY mixed ({vs_ma20:+.1f}% vs MA20, {vs_ma5:+.1f}% vs MA5) — reduced sizing"

        result = {
            "regime":         regime,
            "spy_price":      round(spy_price, 2),
            "spy_change_pct": round(spy_change_pct, 2),
            "spy_vs_ma5":     round(vs_ma5, 2),
            "spy_vs_ma20":    round(vs_ma20, 2),
            "spy_vs_ma50":    round(vs_ma50, 2),
            "min_ai_score":   min_ai_score,
            "size_factor":    size_factor,
            "max_positions":  max_positions,
            "block_buys":     block_buys,
            "reason":         reason,
            "fetched_at":     datetime.now(timezone.utc).timestamp(),
        }

        # ── Asymmetric smoothing: deterioration is immediate, recovery needs confirmation ──
        prev = _load_cache_raw()
        prev_regime   = (prev or {}).get("regime", regime)
        cur_severity  = _REGIME_SEVERITY.get(regime, 1)
        prev_severity = _REGIME_SEVERITY.get(prev_regime, 1)

        if cur_severity >= prev_severity:
            # Deterioration or same level → apply immediately, clear pending
            result["pending_regime"] = None
            result["pending_count"]  = 0
            _save_cache(result)
            return result
        else:
            # Recovery → require N consecutive confirmations before upgrading
            pending_regime = (prev or {}).get("pending_regime")
            pending_count  = (prev or {}).get("pending_count", 0)
            pending_count  = pending_count + 1 if pending_regime == regime else 1

            if pending_count >= _UPGRADE_MIN_COUNT:
                # Confirmed recovery — apply upgrade
                result["pending_regime"] = None
                result["pending_count"]  = 0
                _save_cache(result)
                return result
            else:
                # Not confirmed yet — hold previous regime, store pending state
                held = {**(prev or result)}
                held["pending_regime"] = regime
                held["pending_count"]  = pending_count
                held["fetched_at"]     = result["fetched_at"]
                _save_cache(held)
                print(f"[regime] recovery pending: {prev_regime}→{regime} ({pending_count}/{_UPGRADE_MIN_COUNT} confirmed)")
                return {**held, "reason": held["reason"] + f" | upgrade pending: {regime} ({pending_count}/{_UPGRADE_MIN_COUNT})"}


    except Exception as e:
        return _fallback(f"Error fetching SPY: {e}")


def _fallback(reason: str) -> dict:
    """Safe default when SPY data is unavailable — don't block trading."""
    return {
        "regime":         "NEUTRAL",
        "spy_price":      0,
        "spy_change_pct": 0,
        "spy_vs_ma5":     0,
        "spy_vs_ma20":    0,
        "spy_vs_ma50":    0,
        "min_ai_score":   7,
        "size_factor":    1.0,
        "max_positions":  10,
        "block_buys":     False,
        "reason":         reason,
        "fetched_at":     datetime.now(timezone.utc).timestamp(),
    }
