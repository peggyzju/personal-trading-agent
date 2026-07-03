"""Exchange calendar helpers for trading-day and market-hours gates."""
from __future__ import annotations

from datetime import datetime, time as dtime, timezone
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")

_TRADING_DAY_CACHE: dict[str, bool] = {}


def now_et() -> datetime:
    return datetime.now(timezone.utc).astimezone(ET)


def is_trading_day_et(dt: datetime | None = None) -> bool:
    """Return True only when Alpaca reports a real US equity session for the ET date.

    On calendar lookup failure, fail closed. Missing a scan is safer than submitting
    orders on a holiday that weekday-only logic mistakes for a market session.
    """
    target = (dt or now_et()).astimezone(ET)
    if target.weekday() >= 5:
        return False

    date_str = target.date().isoformat()
    if date_str in _TRADING_DAY_CACHE:
        return _TRADING_DAY_CACHE[date_str]

    try:
        from src.trader.alpaca_trader import get_client

        sessions = get_client().get_calendar(start=date_str, end=date_str)
        is_open = bool(sessions)
    except Exception as e:
        print(f"[calendar] Alpaca calendar lookup failed for {date_str}: {e}")
        is_open = False

    _TRADING_DAY_CACHE[date_str] = is_open
    return is_open


def is_market_hours_et(
    dt: datetime | None = None,
    *,
    start: dtime = dtime(9, 31),
    end: dtime = dtime(16, 5),
) -> bool:
    target = (dt or now_et()).astimezone(ET)
    return is_trading_day_et(target) and start <= target.time() <= end
