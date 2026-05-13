"""
Portfolio-level Circuit Breaker

Monitors intraday portfolio loss. If single-day drawdown exceeds the
threshold (default 5%), all buy signals are blocked for the rest of
that trading day. Sell/reduce signals are always allowed through.

State is persisted to data/circuit_breaker.json so it survives
backend restarts. Auto-resets at the start of each new trading day.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path

_BREAKER_FILE   = Path(__file__).parent.parent.parent / "data" / "circuit_breaker.json"
MAX_DAILY_LOSS_PCT = 5.0   # trigger threshold: 5% portfolio loss in one day


def _load() -> dict:
    try:
        if _BREAKER_FILE.exists():
            return json.loads(_BREAKER_FILE.read_text())
    except Exception:
        pass
    return {}


def _save(state: dict):
    try:
        _BREAKER_FILE.parent.mkdir(exist_ok=True)
        _BREAKER_FILE.write_text(json.dumps(state))
    except Exception:
        pass


def get_circuit_breaker_state() -> dict:
    """
    Returns current state:
      triggered   : bool
      reason      : str
      triggered_at: str | None
      daily_loss_pct: float
      reset_at    : str | None  (start of next trading day)
    """
    state = _load()
    today = date.today().isoformat()

    # Auto-reset on new day
    if state.get("date") != today:
        state = {
            "triggered": False,
            "reason": "",
            "triggered_at": None,
            "daily_loss_pct": 0.0,
            "date": today,
        }
        _save(state)

    return state


def check_and_update(portfolio_history: dict) -> dict:
    """
    Called at the start of each agent run.
    Reads today's P&L from portfolio_history and triggers the breaker
    if loss exceeds threshold.

    Returns the (possibly updated) circuit breaker state.
    """
    state = get_circuit_breaker_state()

    # Already triggered today — stay triggered
    if state.get("triggered"):
        return state

    today = date.today().isoformat()
    days  = portfolio_history.get("days", [])
    today_day = next((d for d in reversed(days) if d["date"] == today), None)

    if today_day is None:
        # Also try a live account check
        try:
            from src.trader.alpaca_trader import get_account
            acct = get_account()
            equity = float(acct.equity)
            prev_close_eq = float(getattr(acct, "last_equity", equity))
            if prev_close_eq > 0:
                daily_loss_pct = (equity - prev_close_eq) / prev_close_eq * 100
            else:
                return state
        except Exception:
            return state
    else:
        daily_loss_pct = today_day.get("daily_return_pct", 0.0)

    state["daily_loss_pct"] = round(daily_loss_pct, 3)

    if daily_loss_pct <= -MAX_DAILY_LOSS_PCT:
        now_str = datetime.now(timezone.utc).isoformat()
        state["triggered"] = True
        state["reason"] = (
            f"Daily loss {daily_loss_pct:.2f}% exceeds -{MAX_DAILY_LOSS_PCT}% threshold — "
            f"all buys paused for today"
        )
        state["triggered_at"] = now_str
        print(f"[circuit_breaker] TRIGGERED: {state['reason']}")

    _save(state)
    return state


def reset_breaker():
    """Manually reset the circuit breaker (e.g. via API)."""
    state = get_circuit_breaker_state()
    state["triggered"] = False
    state["reason"] = "Manually reset"
    state["triggered_at"] = None
    _save(state)
    return state
