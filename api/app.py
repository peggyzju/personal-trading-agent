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

# In-memory cache: symbol -> last analysis result
_analysis_cache: dict = {}


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
            results.append(q)
        except Exception as e:
            results.append({"symbol": symbol, "error": str(e)})
    return results


@app.get("/api/quotes/{symbol}")
def get_quote(symbol: str):
    from src.monitor.price_monitor import get_quote as _get_quote
    try:
        q = _get_quote(symbol.upper())
        q["analysis"] = _analysis_cache.get(symbol.upper())
        return q
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


# ── Analysis ──────────────────────────────────────────────────────────────────

@app.post("/api/analyze/{symbol}")
def run_analysis(symbol: str):
    from src.monitor.price_monitor import get_quote as _get_quote, get_ohlcv
    from src.analysis.ai_analyst import analyze

    s = symbol.upper()
    try:
        quote = _get_quote(s)
        ohlcv = get_ohlcv(s)
        result = analyze(s, ohlcv, quote)
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
        api = get_client()
        positions = api.list_positions()
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
        api = get_client()
        orders = api.list_orders(status="all", limit=20)
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
