from __future__ import annotations
import yfinance as yf
from datetime import datetime, timezone


def get_news(symbol: str, limit: int = 8) -> list[dict]:
    ticker = yf.Ticker(symbol)
    raw = ticker.news or []
    results = []
    for item in raw[:limit]:
        content = item.get("content", {})
        pub_raw = content.get("pubDate") or item.get("providerPublishTime")
        if isinstance(pub_raw, int):
            pub_dt = datetime.fromtimestamp(pub_raw, tz=timezone.utc)
            published = pub_dt.isoformat()
        elif isinstance(pub_raw, str):
            published = pub_raw
        else:
            published = ""

        title = content.get("title") or item.get("title", "")
        summary = content.get("summary") or item.get("summary", "")
        url = (content.get("canonicalUrl") or {}).get("url") or item.get("link", "")

        if title:
            results.append({
                "title": title,
                "summary": summary[:300] if summary else "",
                "published": published,
                "url": url,
                "source": (content.get("provider") or {}).get("displayName", ""),
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
