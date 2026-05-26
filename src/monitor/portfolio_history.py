from __future__ import annotations
import random
from datetime import date, timedelta


def get_history() -> dict:
    """Try Alpaca portfolio history; fall back to demo data."""
    try:
        return _get_alpaca_history()
    except Exception:
        return _get_demo_history()


def _is_trading_day(api, date_str: str) -> bool:
    """Return True if date_str is a US market trading day (not weekend/holiday)."""
    try:
        calendars = api.get_calendar(start=date_str, end=date_str)
        return len(calendars) > 0
    except Exception:
        # Fallback: at least skip weekends
        return date.fromisoformat(date_str).weekday() < 5


def _get_alpaca_history() -> dict:
    from src.config import get_alpaca_creds
    import alpaca_trade_api as tradeapi

    api_key, secret_key, base_url = get_alpaca_creds()
    if not api_key or not secret_key:
        raise ValueError("Alpaca keys not configured")

    api = tradeapi.REST(api_key, secret_key, base_url)
    hist = api.get_portfolio_history(period="1A", timeframe="1D", extended_hours=False)

    days = []
    prev_equity = None
    for i, ts in enumerate(hist.timestamp):
        eq = hist.equity[i]
        if eq is None or eq == 0:
            continue
        eq = float(eq)
        if prev_equity is None:
            prev_equity = eq

        pl = eq - prev_equity
        pl_pct = (pl / prev_equity * 100) if prev_equity > 0 else 0
        days.append({
            "date": date.fromtimestamp(ts).isoformat(),
            "equity": round(eq, 2),
            "daily_pl": round(pl, 2),
            "daily_return_pct": round(pl_pct, 3),
        })
        prev_equity = eq

    # Use live account equity for today (portfolio history lags until EOD)
    try:
        live_equity = float(api.get_account().equity)
    except Exception:
        live_equity = None

    current = live_equity or (float(hist.equity[-1]) if hist.equity[-1] else 100_000.0)
    base = float(hist.base_value) if hist.base_value else 100_000.0

    # Patch today's entry with live equity — only on actual trading days
    today_str = date.today().isoformat()
    if days and live_equity and _is_trading_day(api, today_str):
        # Find yesterday's closing equity for today's daily P&L calc
        yesterday_equity = days[-1]["equity"] if days[-1]["date"] != today_str else (days[-2]["equity"] if len(days) > 1 else base)
        today_pl = round(live_equity - yesterday_equity, 2)
        today_pl_pct = round(today_pl / yesterday_equity * 100, 3) if yesterday_equity > 0 else 0
        if days[-1]["date"] == today_str:
            days[-1] = {"date": today_str, "equity": round(live_equity, 2), "daily_pl": today_pl, "daily_return_pct": today_pl_pct}
        else:
            days.append({"date": today_str, "equity": round(live_equity, 2), "daily_pl": today_pl, "daily_return_pct": today_pl_pct})

    return {
        "current_equity": round(current, 2),
        "base_value": round(base, 2),
        "total_pl": round(current - base, 2),
        "total_return_pct": round((current - base) / base * 100, 3),
        "days": days,
        "source": "alpaca",
    }


def _get_demo_history() -> dict:
    """Deterministic demo history from Jan 1 of current year to today."""
    today = date.today()
    start = date(today.year, 1, 1)
    rng = random.Random(today.year)  # deterministic seed

    equity = 100_000.0
    days = []
    d = start
    while d <= today:
        if d.weekday() < 5:
            ret = rng.gauss(0.0006, 0.009)
            prev = equity
            equity = equity * (1 + ret)
            days.append({
                "date": d.isoformat(),
                "equity": round(equity, 2),
                "daily_pl": round(equity - prev, 2),
                "daily_return_pct": round(ret * 100, 3),
            })
        d += timedelta(days=1)

    base = 100_000.0
    return {
        "current_equity": round(equity, 2),
        "base_value": base,
        "total_pl": round(equity - base, 2),
        "total_return_pct": round((equity - base) / base * 100, 3),
        "days": days,
        "source": "demo",
    }
