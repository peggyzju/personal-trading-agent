from src.monitor.price_monitor import get_quote


def get_movers(symbols: list[str]) -> dict:
    quotes = []
    for symbol in symbols:
        try:
            q = get_quote(symbol)
            quotes.append(q)
        except Exception:
            pass

    quotes.sort(key=lambda q: q.get("change_pct", 0), reverse=True)
    return {
        "gainers": [q for q in quotes if q.get("change_pct", 0) > 0],
        "losers":  list(reversed([q for q in quotes if q.get("change_pct", 0) < 0])),
        "all":     quotes,
    }
