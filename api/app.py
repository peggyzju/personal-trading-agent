import json
import os
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

load_dotenv()

app = FastAPI(title="Personal Trading Agent")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

WATCHLIST_FILE = Path(__file__).parent.parent / "watchlist.json"
DEFAULT_WATCHLIST = ["AAPL", "NVDA", "MSFT", "TSLA"]

_analysis_cache: dict = {}   # symbol -> last analysis
_news_cache: dict = {}       # symbol -> {items, sentiment, fetched_at}
_brief_cache: dict = {}      # date -> daily brief


def load_watchlist() -> list[str]:
    if WATCHLIST_FILE.exists():
        return json.loads(WATCHLIST_FILE.read_text())
    return DEFAULT_WATCHLIST


def save_watchlist(symbols: list[str]):
    WATCHLIST_FILE.write_text(json.dumps(symbols))


# ── Watchlist ─────────────────────────────────────────────────────────────────

@app.get("/api/watchlist")
def get_watchlist():
    return {"symbols": load_watchlist()}


@app.post("/api/watchlist/{symbol}")
def add_symbol(symbol: str):
    wl = load_watchlist()
    s = symbol.upper()
    if s not in wl:
        wl.append(s)
        save_watchlist(wl)
    return {"symbols": wl}


@app.delete("/api/watchlist/{symbol}")
def remove_symbol(symbol: str):
    wl = load_watchlist()
    wl = [s for s in wl if s != symbol.upper()]
    save_watchlist(wl)
    return {"symbols": wl}


# ── Quotes ────────────────────────────────────────────────────────────────────

@app.get("/api/quotes")
def get_quotes():
    from src.monitor.price_monitor import get_quote
    results = []
    for symbol in load_watchlist():
        try:
            q = get_quote(symbol)
            q["analysis"] = _analysis_cache.get(symbol)
            q["news_sentiment"] = (_news_cache.get(symbol) or {}).get("overall")
            results.append(q)
        except Exception as e:
            results.append({"symbol": symbol, "error": str(e)})
    return results


@app.get("/api/quotes/{symbol}")
def get_quote_single(symbol: str):
    from src.monitor.price_monitor import get_quote as _get_quote
    try:
        q = _get_quote(symbol.upper())
        q["analysis"] = _analysis_cache.get(symbol.upper())
        q["news_sentiment"] = (_news_cache.get(symbol.upper()) or {}).get("overall")
        return q
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


# ── Analysis (price + news) ───────────────────────────────────────────────────

@app.post("/api/analyze/{symbol}")
def run_analysis(symbol: str):
    from src.monitor.price_monitor import get_quote as _get_quote, get_ohlcv
    from src.monitor.news_monitor import get_news
    from src.analysis.ai_analyst import analyze

    s = symbol.upper()
    try:
        quote = _get_quote(s)
        ohlcv = get_ohlcv(s)
        news = get_news(s)
        result = analyze(s, ohlcv, quote, news=news)
        result["symbol"] = s
        result["price"] = quote["price"]
        result["change_pct"] = quote["change_pct"]
        _analysis_cache[s] = result
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/analysis/cache")
def get_cached_analyses():
    return _analysis_cache


# ── News & Sentiment ──────────────────────────────────────────────────────────

@app.get("/api/news/{symbol}")
def get_news_for_symbol(symbol: str):
    from src.monitor.news_monitor import get_news
    s = symbol.upper()
    try:
        items = get_news(s)
        _news_cache.setdefault(s, {})["items"] = items
        return {"symbol": s, "items": items}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/news/{symbol}/sentiment")
def analyze_news_sentiment(symbol: str):
    from src.monitor.news_monitor import get_news
    from src.monitor.price_monitor import get_quote as _get_quote
    from src.analysis.sentiment_analyzer import analyze_news_sentiment as _analyze

    s = symbol.upper()
    try:
        news = get_news(s)
        quote = _get_quote(s)
        result = _analyze(s, news, quote["change_pct"])
        result["symbol"] = s
        _news_cache[s] = result
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/news/cache/all")
def get_news_cache():
    return _news_cache


# ── Market Movers ─────────────────────────────────────────────────────────────

@app.get("/api/movers")
def get_movers():
    from src.monitor.market_movers import get_movers as _get_movers
    try:
        return _get_movers(load_watchlist())
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Earnings Calendar ─────────────────────────────────────────────────────────

@app.get("/api/earnings/{symbol}")
def get_earnings(symbol: str):
    from src.monitor.news_monitor import get_earnings_calendar
    try:
        return {"symbol": symbol.upper(), "calendar": get_earnings_calendar(symbol.upper())}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Daily Brief ───────────────────────────────────────────────────────────────

@app.post("/api/brief")
def generate_brief():
    from datetime import date
    from src.monitor.price_monitor import get_quote as _get_quote
    from src.monitor.news_monitor import get_news
    from src.analysis.daily_brief import generate_daily_brief

    today = date.today().isoformat()
    if today in _brief_cache:
        return _brief_cache[today]

    watchlist = load_watchlist()
    watchlist_data = []
    for symbol in watchlist:
        try:
            quote = _get_quote(symbol)
            news = get_news(symbol, limit=5)
            watchlist_data.append({
                "symbol": symbol,
                "price": quote["price"],
                "change_pct": quote["change_pct"],
                "news": news,
                "analysis": _analysis_cache.get(symbol),
            })
        except Exception:
            pass

    result = generate_daily_brief(watchlist_data)
    _brief_cache[today] = result
    return result


@app.get("/api/brief")
def get_brief():
    from datetime import date
    today = date.today().isoformat()
    if today in _brief_cache:
        return _brief_cache[today]
    raise HTTPException(status_code=404, detail="No brief yet. POST /api/brief to generate.")


# ── Portfolio / Positions ─────────────────────────────────────────────────────

@app.get("/api/account")
def get_account():
    from src.trader.alpaca_trader import get_account as _get_account
    try:
        acct = _get_account()
        return {
            "equity": float(acct.equity),
            "buying_power": float(acct.buying_power),
            "cash": float(acct.cash),
            "portfolio_value": float(acct.portfolio_value),
            "daytrade_count": acct.daytrade_count,
            "status": acct.status,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/positions")
def get_positions():
    from src.trader.alpaca_trader import get_client
    try:
        alpaca = get_client()
        positions = alpaca.list_positions()
        return [
            {
                "symbol": p.symbol,
                "qty": float(p.qty),
                "avg_entry_price": float(p.avg_entry_price),
                "current_price": float(p.current_price),
                "market_value": float(p.market_value),
                "unrealized_pl": float(p.unrealized_pl),
                "unrealized_plpc": float(p.unrealized_plpc) * 100,
                "side": p.side,
            }
            for p in positions
        ]
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/orders")
def get_orders():
    from src.trader.alpaca_trader import get_client
    try:
        alpaca = get_client()
        orders = alpaca.list_orders(status="all", limit=20)
        return [
            {
                "id": o.id,
                "symbol": o.symbol,
                "side": o.side,
                "qty": float(o.qty),
                "filled_qty": float(o.filled_qty) if o.filled_qty else 0,
                "filled_avg_price": float(o.filled_avg_price) if o.filled_avg_price else None,
                "status": o.status,
                "created_at": str(o.created_at),
                "type": o.type,
            }
            for o in orders
        ]
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Serve built React app ─────────────────────────────────────────────────────

frontend_dist = Path(__file__).parent.parent / "frontend" / "dist"
if frontend_dist.exists():
    app.mount("/", StaticFiles(directory=str(frontend_dist), html=True), name="static")
