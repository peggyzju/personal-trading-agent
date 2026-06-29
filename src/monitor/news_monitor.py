from __future__ import annotations
import yfinance as yf
from datetime import datetime, timezone


def get_news(symbol: str, limit: int = 8) -> list[dict]:
    """近 14 天公司新闻 — 走 Finnhub company-news(根治 yfinance 限流)。失败返回 []。"""
    import json
    import urllib.parse
    import urllib.request
    from datetime import date, timedelta
    from src.config import get_finnhub_key

    key = get_finnhub_key()
    if not key:
        return []
    today = date.today()
    params = {"symbol": symbol, "from": (today - timedelta(days=14)).isoformat(),
              "to": today.isoformat(), "token": key}
    url = f"https://finnhub.io/api/v1/company-news?{urllib.parse.urlencode(params)}"
    try:
        with urllib.request.urlopen(url, timeout=15) as r:
            raw = json.loads(r.read())
    except Exception:
        return []
    if not isinstance(raw, list):
        return []

    results = []
    for item in raw[:limit]:
        title = item.get("headline") or ""
        if not title:
            continue
        ts = item.get("datetime")
        published = (datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
                     if isinstance(ts, (int, float)) and ts else "")
        results.append({
            "title": title,
            "summary": (item.get("summary") or "")[:300],
            "published": published,
            "url": item.get("url") or "",
            "source": item.get("source") or "",
        })
    return results


def get_earnings_calendar(symbol: str) -> dict | None:
    ticker = yf.Ticker(symbol)
    try:
        cal = ticker.calendar
        if cal is None:
            return None
        if hasattr(cal, "to_dict"):
            return cal.to_dict()
        if isinstance(cal, dict):
            return {k: str(v) for k, v in cal.items()}
    except Exception:
        pass
    return None


def earnings_within_days(symbol: str, days: int = 3) -> tuple[bool, str]:
    """
    Returns (True, date_str) if the stock has earnings within `days` calendar days.
    Returns (False, "") otherwise.

    Use before queuing a buy trade to avoid holding through earnings surprise.
    """
    from datetime import date, timedelta
    try:
        ticker = yf.Ticker(symbol)
        cal = ticker.calendar
        if cal is None:
            return False, ""

        # yfinance returns calendar as dict with "Earnings Date" key
        # The value can be a list of dates or a single date
        earnings_date = None
        if isinstance(cal, dict):
            ed = cal.get("Earnings Date")
            if ed is None:
                ed = cal.get("earningsDate")
            if isinstance(ed, list) and len(ed) > 0:
                # Take the nearest upcoming date
                today = date.today()
                upcoming = [d for d in ed if hasattr(d, "date") and d.date() >= today]
                if not upcoming:
                    upcoming = [d for d in ed if isinstance(d, date) and d >= today]
                earnings_date = min(upcoming) if upcoming else None
            elif ed is not None:
                earnings_date = ed

        if earnings_date is None:
            return False, ""

        # Normalise to date object
        if hasattr(earnings_date, "date"):
            earnings_date = earnings_date.date()

        today = date.today()
        delta = (earnings_date - today).days
        if 0 <= delta <= days:
            return True, str(earnings_date)
        return False, ""

    except Exception:
        return False, ""
