from __future__ import annotations
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
    """下一次财报(未来 ~90 天)。走 Finnhub /calendar/earnings(替代 yfinance)。"""
    import json
    import urllib.parse
    import urllib.request
    from datetime import date, timedelta
    from src.config import get_finnhub_key
    try:
        key = get_finnhub_key()
        if not key:
            return None
        today = date.today()
        qs = urllib.parse.urlencode({
            "from": today.isoformat(),
            "to": (today + timedelta(days=90)).isoformat(),
            "symbol": symbol,
            "token": key,
        })
        url = f"https://finnhub.io/api/v1/calendar/earnings?{qs}"
        with urllib.request.urlopen(url, timeout=15) as r:
            data = json.loads(r.read().decode())
        cal = (data or {}).get("earningsCalendar", []) if isinstance(data, dict) else []
        nxt = sorted(cal, key=lambda e: e.get("date") or "")
        return nxt[0] if nxt else None
    except Exception:
        return None


def earnings_within_days(symbol: str, days: int = 3) -> tuple[bool, str]:
    """
    未来 `days` 个日历日内有未公布财报 → (True, date_str),否则 (False, "")。
    买入前调用,避免持仓穿越财报。数据走 **Finnhub /calendar/earnings**(替代 yfinance)。
    取数失败/无 key → 保守返回 (False, "") = 不阻塞买入(降级安全)。
    """
    import json
    import urllib.parse
    import urllib.request
    from datetime import date, timedelta
    from src.config import get_finnhub_key
    try:
        key = get_finnhub_key()
        if not key:
            return False, ""
        today = date.today()
        qs = urllib.parse.urlencode({
            "from": today.isoformat(),
            "to": (today + timedelta(days=days)).isoformat(),
            "symbol": symbol,
            "token": key,
        })
        url = f"https://finnhub.io/api/v1/calendar/earnings?{qs}"
        with urllib.request.urlopen(url, timeout=15) as r:
            data = json.loads(r.read().decode())
        cal = (data or {}).get("earningsCalendar", []) if isinstance(data, dict) else []
        # from/to 已限定窗口;任一条目即表示 days 天内有财报。取最近日期。
        dates = sorted(e["date"] for e in cal if e.get("date"))
        if dates:
            return True, dates[0]
        return False, ""
    except Exception:
        return False, ""
