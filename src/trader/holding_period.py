from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")


def parse_et_datetime(value) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        text = str(value).strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(text)
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(ET)


def trading_days_since_entry(entry_at, now: datetime | None = None) -> int | None:
    entry_dt = parse_et_datetime(entry_at)
    if entry_dt is None:
        return None

    now_dt = now.astimezone(ET) if now else datetime.now(ET)
    start = entry_dt.date()
    end = now_dt.date()
    if end <= start:
        return 0

    days = 0
    current = start + timedelta(days=1)
    while current <= end:
        if current.weekday() < 5:
            days += 1
        current += timedelta(days=1)
    return days
