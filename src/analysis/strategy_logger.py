"""
Strategy Logger
===============
Appends a daily snapshot to data/strategy_log.json each time it's called.
Records:
  - Scan quality  : RSI/vMA20 distribution, pass/block counts
  - Signal quality: signal mix, AI score stats
  - Trade results : closed trades today (P&L, win rate, profit factor)
  - Portfolio state: allocation, float P&L

Call once per day (e.g. after the noon scan, or at EOD).
"""
from __future__ import annotations

import json
import os
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Optional

import numpy as np

_LOG_PATH = Path(__file__).parents[2] / "data" / "strategy_log.json"


# ── Helpers ───────────────────────────────────────────────────────────────────

def _load_log() -> list[dict]:
    try:
        return json.loads(_LOG_PATH.read_text())
    except Exception:
        return []


def _save_log(entries: list[dict]) -> None:
    _LOG_PATH.write_text(json.dumps(entries, indent=2, default=str))


def _pct_buckets(values: list[float], buckets: list[tuple]) -> dict:
    """Count values falling into each named bucket."""
    result = {}
    for label, lo, hi in buckets:
        result[label] = sum(1 for v in values if lo <= v < hi)
    return result


# ── Scan quality snapshot ────────────────────────────────────────────────────

def _scan_quality(candidates: list[dict]) -> dict:
    """Summarise RSI and vMA20 distribution of scan candidates."""
    if not candidates:
        return {}

    rsi_vals  = [c["rsi"] for c in candidates if c.get("rsi") is not None]
    ma20_vals = [c["vs_ma20_pct"] for c in candidates if c.get("vs_ma20_pct") is not None]

    rsi_buckets = _pct_buckets(rsi_vals, [
        ("rsi_below40",  0,  40),
        ("rsi_40_55",   40,  55),   # sweet spot
        ("rsi_55_60",   55,  60),
        ("rsi_60_65",   60,  65),
        ("rsi_above65", 65, 200),   # should be 0 with new filter
    ])

    ma20_buckets = _pct_buckets(ma20_vals, [
        ("vma20_neg",    -999,  0),
        ("vma20_0_3",       0,  3),  # ideal
        ("vma20_3_5",       3,  5),
        ("vma20_5_8",       5,  8),
        ("vma20_above8",    8, 999), # should be 0 with new filter
    ])

    signal_counts: dict[str, int] = {}
    for c in candidates:
        sig = c.get("signal", "HOLD")
        signal_counts[sig] = signal_counts.get(sig, 0) + 1

    ai_scores = [c["ai_score"] for c in candidates if c.get("ai_score") is not None]

    return {
        "total_candidates": len(candidates),
        "rsi_mean":   round(float(np.mean(rsi_vals)),  1) if rsi_vals  else None,
        "rsi_dist":   rsi_buckets,
        "vma20_mean": round(float(np.mean(ma20_vals)), 1) if ma20_vals else None,
        "vma20_dist": ma20_buckets,
        "signal_counts": signal_counts,
        "ai_score_mean": round(float(np.mean(ai_scores)), 1) if ai_scores else None,
        "quality_score": _compute_quality_score(rsi_vals, ma20_vals),
    }


def _compute_quality_score(rsi_vals: list, ma20_vals: list) -> float:
    """
    0-100 score: higher = better entry quality candidates.
    Rewards RSI 40-60 and vMA20 0-5%; penalises extremes.
    """
    if not rsi_vals:
        return 0.0
    rsi_ok  = sum(1 for r in rsi_vals if 40 <= r <= 62) / len(rsi_vals)
    ma20_ok = sum(1 for m in ma20_vals if 0 <= m <= 5)  / len(ma20_vals) if ma20_vals else 0
    return round((rsi_ok * 0.6 + ma20_ok * 0.4) * 100, 1)


# ── Closed-trade P&L for today ────────────────────────────────────────────────

def _trade_results_today(alpaca_api) -> dict:
    """Fetch today's closed trades from Alpaca and compute stats."""
    from datetime import timedelta

    try:
        today_start = datetime.now(timezone.utc).replace(
            hour=0, minute=0, second=0, microsecond=0
        ).isoformat()
        orders = alpaca_api.list_orders(
            status="closed",
            after=today_start,
            limit=100,
            direction="asc",
        )
        sells = [
            o for o in orders
            if o.side == "sell" and o.status == "filled" and o.filled_avg_price
        ]
        if not sells:
            return {"closed_trades_today": 0}

        # Match sells to their buy prices via position history (best-effort)
        # We use filled_avg_price of the sell order vs the last known cost basis.
        # For now we just record what closed today and report notional.
        return {
            "closed_trades_today": len(sells),
            "symbols_closed": list({o.symbol for o in sells}),
        }
    except Exception as e:
        return {"closed_trades_today": None, "error": str(e)}


# ── Portfolio state ──────────────────────────────────────────────────────────

def _portfolio_state(alpaca_api) -> dict:
    """Current account snapshot."""
    try:
        acct      = alpaca_api.get_account()
        positions = alpaca_api.list_positions()

        equity    = float(acct.equity)
        invested  = sum(float(p.market_value) for p in positions)
        cash      = max(0.0, equity - invested)
        float_pnl = sum(float(p.unrealized_pl) for p in positions)
        float_pct = float_pnl / equity * 100 if equity else 0

        return {
            "equity":       round(equity, 0),
            "cash":         round(cash, 0),
            "invested":     round(invested, 0),
            "alloc_pct":    round(invested / equity * 100, 1) if equity else 0,
            "float_pnl":    round(float_pnl, 0),
            "float_pct":    round(float_pct, 2),
            "positions":    len(positions),
        }
    except Exception as e:
        return {"error": str(e)}


# ── Cumulative closed-trade stats (rolling from log history) ─────────────────

def _rolling_stats(log: list[dict], days: int = 14) -> dict:
    """Aggregate closed trade P&L from previous log entries."""
    recent = [e for e in log[-days:] if e.get("closed_pnl_pct") is not None]
    if not recent:
        return {}
    pnls = [e["closed_pnl_pct"] for e in recent]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    return {
        f"rolling_{days}d_trades":   len(pnls),
        f"rolling_{days}d_win_rate": round(len(wins) / len(pnls) * 100, 1) if pnls else 0,
        f"rolling_{days}d_avg_win":  round(float(np.mean(wins)),   2) if wins   else 0,
        f"rolling_{days}d_avg_loss": round(float(np.mean(losses)), 2) if losses else 0,
        f"rolling_{days}d_pf":       round(abs(sum(wins) / sum(losses)), 2) if sum(losses) != 0 else None,
    }


# ── Public entry point ───────────────────────────────────────────────────────

def record_daily_snapshot(
    candidates: list[dict],
    alpaca_api=None,
    closed_pnl_pct: Optional[float] = None,   # pass if you know today's realised P&L
    extra: Optional[dict] = None,
) -> dict:
    """
    Append one entry to strategy_log.json.
    Returns the entry that was written.
    """
    today = str(date.today())
    log   = _load_log()

    # Remove any existing entry for today (idempotent — last write wins)
    log = [e for e in log if e.get("date") != today]

    scan_q    = _scan_quality(candidates)
    portfolio = _portfolio_state(alpaca_api) if alpaca_api else {}
    trades    = _trade_results_today(alpaca_api) if alpaca_api else {}
    rolling   = _rolling_stats(log)

    entry: dict = {
        "date":            today,
        "recorded_at":     datetime.now(timezone.utc).isoformat(),
        "scan_quality":    scan_q,
        "portfolio":       portfolio,
        "trades_today":    trades,
        "closed_pnl_pct":  closed_pnl_pct,   # caller supplies if known
        **rolling,
        **(extra or {}),
    }

    log.append(entry)
    _save_log(log)

    print(f"[strategy_logger] Snapshot saved for {today} | "
          f"quality_score={scan_q.get('quality_score')} | "
          f"candidates={scan_q.get('total_candidates')} | "
          f"rsi_mean={scan_q.get('rsi_mean')} | "
          f"equity=${portfolio.get('equity','?')}")
    return entry


def get_log(days: int = 30) -> list[dict]:
    """Return last N days of log entries."""
    log = _load_log()
    return log[-days:]
