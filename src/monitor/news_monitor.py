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
