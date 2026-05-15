from __future__ import annotations
import json
import os
import threading
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, BackgroundTasks
from pydantic import BaseModel
from typing import Optional
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

load_dotenv()


@asynccontextmanager
async def _lifespan(app: FastAPI):
    _start_scheduler()
    yield


app = FastAPI(title="Personal Trading Agent", lifespan=_lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:5174"],
    allow_methods=["*"],
    allow_headers=["*"],
)

WATCHLIST_FILE = Path(__file__).parent.parent / "watchlist.json"
DEFAULT_WATCHLIST = ["AAPL", "NVDA", "MSFT", "TSLA"]
_SCAN_CACHE_FILE = Path(__file__).parent.parent / "data" / "scan_cache.json"

_analysis_cache: dict = {}           # symbol -> analysis dict
_analysis_timestamps: dict = {}      # symbol -> unix timestamp of last update
_news_cache: dict = {}
_brief_cache: dict = {}
_holdings_cache: dict = {}           # "sell_signals" -> list, "positions" -> list
_scan_running: bool = False


def _load_scan_cache() -> dict:
    try:
        if _SCAN_CACHE_FILE.exists():
            return json.loads(_SCAN_CACHE_FILE.read_text())
    except Exception:
        pass
    return {}

def _save_scan_cache(cache: dict):
    try:
        _SCAN_CACHE_FILE.parent.mkdir(exist_ok=True)
        _SCAN_CACHE_FILE.write_text(json.dumps(cache))
    except Exception as e:
        print(f"[scan] cache save error: {e}")

_scan_cache: dict = _load_scan_cache()   # restore from disk on startup


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


# ── Analysis ──────────────────────────────────────────────────────────────────

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
        import time as _time
        _analysis_cache[s] = result
        _analysis_timestamps[s] = _time.time()
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


# ── Market Movers ─────────────────────────────────────────────────────────────

@app.get("/api/movers")
def get_movers():
    from src.monitor.market_movers import get_movers as _get_movers
    try:
        return _get_movers(load_watchlist())
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Earnings ─────────────────────────────────────────────────────────────────

@app.get("/api/earnings/{symbol}")
def get_earnings(symbol: str):
    from src.monitor.news_monitor import get_earnings_calendar
    try:
        return {"symbol": symbol.upper(), "calendar": get_earnings_calendar(symbol.upper())}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Daily Brief ───────────────────────────────────────────────────────────────

def _run_brief():
    from datetime import date
    from src.monitor.price_monitor import get_quote as _get_quote
    from src.monitor.news_monitor import get_news
    from src.analysis.daily_brief import generate_daily_brief

    today = date.today().isoformat()
    _brief_cache["status"] = "running"
    try:
        watchlist_data = []
        for symbol in load_watchlist():
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
        result["generated_at"] = today
        _brief_cache[today] = result
        _brief_cache["status"] = "done"
    except Exception as e:
        _brief_cache["status"] = "error"
        _brief_cache["error"] = str(e)


@app.post("/api/brief")
def generate_brief(background_tasks: BackgroundTasks):
    from datetime import date
    today = date.today().isoformat()
    if today in _brief_cache:
        return _brief_cache[today]
    if _brief_cache.get("status") == "running":
        return {"status": "running"}
    background_tasks.add_task(_run_brief)
    return {"status": "running"}


@app.get("/api/brief")
def get_brief():
    from datetime import date
    today = date.today().isoformat()
    if today in _brief_cache:
        return _brief_cache[today]
    status = _brief_cache.get("status")
    if status == "running":
        return {"status": "running"}
    raise HTTPException(status_code=404, detail="No brief yet.")


# ── S&P 500 Scanner ───────────────────────────────────────────────────────────

def _sanitize_floats(obj):
    """Recursively replace NaN/Inf with None so JSON serialization never fails."""
    import math
    if isinstance(obj, float):
        return None if (math.isnan(obj) or math.isinf(obj)) else obj
    if isinstance(obj, dict):
        return {k: _sanitize_floats(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_sanitize_floats(v) for v in obj]
    return obj


def _run_sp500_scan(cascade_agent: bool = False):
    """Run scan. If cascade_agent=True, auto-trigger agent after scan completes."""
    global _scan_running
    from datetime import datetime
    from src.monitor.sp500_scanner import get_scan_universe, quick_screen
    from src.analysis.stock_screener import ai_score_candidates
    from src.analysis.market_context import load_market_context

    _scan_running = True
    _scan_cache["sp500"] = {"status": "running", "candidates": [], "scanned_at": None}
    try:
        # Load market context for sector bias adjustment
        ctx = load_market_context()
        sector_bias = ctx.get("sector_bias", {})

        # Map sector names to stock symbols for bias lookup
        SECTOR_MAP = {
            "tech":          ["AAPL","MSFT","GOOGL","META","NVDA","AMD","AVGO","ORCL"],
            "semiconductors":["NVDA","AMD","AVGO","QCOM","INTC","MU","AMAT","KLAC","LRCX","MRVL","SOXX"],
            "healthcare":    ["UNH","JNJ","LLY","ABBV","MRK","TMO","ABT","DHR","ISRG","VRTX"],
            "energy":        ["XOM","CVX","COP","SLB","EOG","PXD","MPC","VLO","PSX"],
            "financials":    ["JPM","BAC","WFC","GS","MS","BLK","AXP","SPGI"],
            "industrials":   ["CAT","HON","UPS","BA","RTX","LMT","DE","MMM","GE"],
            "consumer":      ["AMZN","TSLA","HD","MCD","NKE","SBUX","TGT","LOW"],
            "utilities":     ["NEE","DUK","SO","AEP","EXC","SRE"],
        }
        # Build symbol → bias dict
        symbol_bias: dict[str, str] = {}
        for sector, bias in sector_bias.items():
            if bias != "neutral":
                for sym in SECTOR_MAP.get(sector, []):
                    # positive sector wins over neutral, but negative always overrides
                    if sym not in symbol_bias or bias == "negative":
                        symbol_bias[sym] = bias

        print("[scan] Building scan universe (S&P 500 + NASDAQ-100 + Layer 2)…")
        from src.monitor.sp500_scanner import get_sp500_tickers, get_nasdaq100_tickers, LAYER2_TICKERS
        tickers = get_scan_universe()
        # Build lookup sets for universe tagging
        _sp500_set   = set(get_sp500_tickers())
        _nasdaq_set  = set(get_nasdaq100_tickers())
        _layer2_set  = set(LAYER2_TICKERS)
        print(f"[scan] Quick-screening {len(tickers)} tickers…")
        top_tech = quick_screen(tickers, top_n=25)
        # Tag each candidate with its primary universe
        for c in top_tech:
            sym = c["symbol"]
            if sym in _sp500_set:
                c["universe"] = "sp500"
            elif sym in _nasdaq_set:
                c["universe"] = "nasdaq100"
            elif sym in _layer2_set:
                c["universe"] = "layer2"
            else:
                c["universe"] = "other"
        print(f"[scan] {len(top_tech)} passed technical filter. Enriching with fundamentals…")
        from src.monitor.sp500_scanner import enrich_with_fundamentals
        top_tech = enrich_with_fundamentals(top_tech)
        print(f"[scan] Fundamentals enriched. Running AI scoring…")
        top_ai = _sanitize_floats(ai_score_candidates(top_tech))

        # Apply sector bias: adjust ai_score ±1 based on sector momentum
        if symbol_bias and top_ai:
            for c in top_ai:
                bias = symbol_bias.get(c["symbol"])
                if bias == "positive" and c.get("ai_score", 0) < 10:
                    c["ai_score"] = min(10, c["ai_score"] + 1)
                    c["reason"] = f"[Sector momentum ↑] {c.get('reason','')}"
                elif bias == "negative" and c.get("ai_score", 0) > 0:
                    c["ai_score"] = max(0, c["ai_score"] - 1)
                    c["reason"] = f"[Sector headwind ↓] {c.get('reason','')}"
            # Re-sort after adjustment
            top_ai = sorted(top_ai, key=lambda x: (
                0 if x.get("signal") in ("STRONG_BUY","BUY") else 1,
                -x.get("ai_score", 0)
            ))

        _scan_cache["sp500"] = {
            "status": "done",
            "candidates": top_ai,
            "scanned_at": datetime.utcnow().isoformat(),
            "total_screened": len(tickers),
            "tech_passed": len(top_tech),
        }
        _save_scan_cache(_scan_cache)
        print(f"[scan] Done. Top candidate: {top_ai[0]['symbol'] if top_ai else 'none'} | sector_bias applied to {sum(1 for s in symbol_bias if any(c['symbol']==s for c in top_ai))} stocks")
    except Exception as e:
        _scan_cache["sp500"] = {"status": "error", "error": str(e), "candidates": []}
    finally:
        _scan_running = False

    # Cascade: auto-run agent after scan completes (both scheduled and manual)
    if cascade_agent and _scan_cache.get("sp500", {}).get("status") == "done":
        print("[scan] Cascading to trade agent…")
        _run_agent_internal()


@app.get("/api/scan/sp500")
def get_scan():
    result = dict(_scan_cache.get("sp500", {"status": "not_run", "candidates": []}))
    # Mark owned symbols instead of filtering — user can still see AI analysis for held positions
    try:
        from src.trader.alpaca_trader import get_client
        owned = {p.symbol for p in get_client().list_positions()}
        if owned and result.get("candidates"):
            result["candidates"] = [
                {**c, "owned": c["symbol"] in owned}
                for c in result["candidates"]
            ]
    except Exception:
        pass
    return result


@app.post("/api/scan/enrich")
def enrich_scan():
    """Enrich existing cached candidates with yfinance fundamentals (P/E, market cap, beta, etc.)."""
    result = _scan_cache.get("sp500", {})
    candidates = result.get("candidates", [])
    if not candidates:
        return {"status": "no_candidates", "candidates": []}
    # Skip if already enriched
    if any(c.get("company_name") for c in candidates[:5]):
        return {"status": "already_enriched", **result}
    try:
        from src.monitor.sp500_scanner import enrich_with_fundamentals
        enriched = _sanitize_floats(enrich_with_fundamentals(list(candidates)))
        _scan_cache["sp500"]["candidates"] = enriched
        _save_scan_cache(_scan_cache)
        return {"status": "done", **_scan_cache["sp500"]}
    except Exception as e:
        return {"status": "error", "error": str(e), "candidates": candidates}


@app.get("/api/scan/nasdaq")
def get_scan_nasdaq():
    """Return scan candidates from NASDAQ-100 universe."""
    result = dict(_scan_cache.get("sp500", {"status": "not_run", "candidates": []}))
    try:
        from src.trader.alpaca_trader import get_client
        from src.monitor.sp500_scanner import get_nasdaq100_tickers
        owned = {p.symbol for p in get_client().list_positions()}
        nasdaq_set = set(get_nasdaq100_tickers())
        if result.get("candidates"):
            result["candidates"] = [
                {**c, "owned": c["symbol"] in owned}
                for c in result["candidates"]
                if c.get("universe") in ("nasdaq100",) or c["symbol"] in nasdaq_set
            ]
    except Exception:
        pass
    return result


@app.get("/api/goal/progress")
def get_goal_progress():
    """Return current goal progress for the 20-day target."""
    try:
        from src.trader.alpaca_trader import get_client
        equity = float(get_client().get_account().equity)
    except Exception:
        # Fallback: read from market_context.json
        try:
            ctx_file = Path("data/market_context.json")
            ctx_data = json.loads(ctx_file.read_text()) if ctx_file.exists() else {}
            equity = ctx_data.get("goal_context", {}).get("current_equity", 100_000.0)
        except Exception:
            equity = 100_000.0
    from src.analysis.market_context import _compute_goal_context, _load_goal
    goal = _load_goal()
    progress = _compute_goal_context(equity)
    return {
        **progress,
        "target_pct_low":  goal.get("target_pct_low", 10.0),
        "target_pct_high": goal.get("target_pct_high", 15.0),
        "total_days":      goal.get("total_days", 20),
        "start_date":      goal.get("start_date", ""),
    }


@app.post("/api/scan/sp500")
def trigger_scan(background_tasks: BackgroundTasks):
    """Manual scan trigger — cascades to agent automatically after scan."""
    global _scan_running
    if _scan_running:
        return {"status": "already_running"}
    background_tasks.add_task(_run_sp500_scan, True)   # cascade_agent=True
    return {"status": "started", "cascade": "agent will auto-run after scan"}


@app.get("/api/pipeline/status")
def get_pipeline_status():
    """Current state of the full pipeline — for UI status display."""
    from datetime import datetime
    from pathlib import Path

    def _age_str(iso: str | None) -> str | None:
        if not iso:
            return None
        try:
            delta = datetime.utcnow() - datetime.fromisoformat(iso.replace("Z",""))
            mins = int(delta.total_seconds() / 60)
            return f"{mins}分钟前" if mins < 60 else f"{mins//60}小时前"
        except Exception:
            return iso

    # Market context
    ctx_file = Path("data/market_context.json")
    ctx_data = {}
    if ctx_file.exists():
        try:
            import json as _json
            ctx_data = _json.loads(ctx_file.read_text())
        except Exception:
            pass

    # Scan
    scan = _scan_cache.get("sp500", {})

    # Agent log
    from src.trader.trade_agent import get_agent_log, get_pending_trades
    log = get_agent_log()
    last_run = log[0] if log else None
    pending = [t for t in get_pending_trades() if t["status"] == "pending"]

    # Review
    review_cache_val = _review_cache.get("latest") or {}

    return {
        "market_context": {
            "status": "done" if ctx_data else "not_run",
            "regime": ctx_data.get("regime"),
            "aggression": ctx_data.get("aggression"),
            "min_ai_score": ctx_data.get("min_ai_score"),
            "generated_at": ctx_data.get("generated_at"),
            "age": _age_str(ctx_data.get("generated_at")),
        },
        "scan": {
            "status": scan.get("status", "not_run"),
            "total_screened": scan.get("total_screened"),
            "candidates": len(scan.get("candidates", [])),
            "scanned_at": scan.get("scanned_at"),
            "age": _age_str(scan.get("scanned_at")),
        },
        "agent": {
            "status": "running" if False else ("done" if last_run else "not_run"),
            "last_run_at": last_run.get("run_at") if last_run else None,
            "age": _age_str(last_run.get("run_at")) if last_run else None,
            "signals_found": last_run.get("signals_found", 0) if last_run else 0,
            "trades_queued": last_run.get("trades_queued", 0) if last_run else 0,
            "pending_approval": len(pending),
        },
        "review": {
            "status": "done" if review_cache_val else "not_run",
            "generated_at": review_cache_val.get("generated_at"),
            "age": _age_str(review_cache_val.get("generated_at")),
            "one_line": review_cache_val.get("one_line_summary"),
        },
    }


# ── Holdings Monitor ──────────────────────────────────────────────────────────

def _refresh_holdings():
    """Background task: re-fetch positions + sell signals after any trade action."""
    from src.monitor.holdings_monitor import get_paper_positions, analyze_sell_signals
    from src.trader.trade_agent import reject_trade, get_pending_trades
    positions = get_paper_positions()
    _holdings_cache["positions"] = positions
    _holdings_cache["analyzed"] = False
    try:
        enriched = analyze_sell_signals(positions)
        _holdings_cache["positions"] = enriched
        _holdings_cache["analyzed"] = True
    except Exception as e:
        print(f"[holdings] auto-refresh error: {e}")

    # Auto-reject pending sell/reduce trades for symbols no longer held
    held = {p["symbol"] for p in positions}
    for trade in get_pending_trades():
        if trade["status"] == "pending" and trade["side"] == "sell" and trade["symbol"] not in held:
            try:
                reject_trade(trade["id"])
                print(f"[holdings] auto-rejected stale sell for {trade['symbol']} (position closed)")
            except Exception:
                pass


@app.get("/api/scan/holdings")
def get_holdings():
    return {
        "positions": _holdings_cache.get("positions", []),
        "analyzed": _holdings_cache.get("analyzed", False),
    }


@app.post("/api/scan/holdings")
def refresh_holdings(background_tasks: BackgroundTasks):
    def _run():
        from src.monitor.holdings_monitor import get_paper_positions, analyze_sell_signals
        positions = get_paper_positions()
        _holdings_cache["positions"] = positions
        _holdings_cache["analyzed"] = False
        try:
            enriched = analyze_sell_signals(positions)
            _holdings_cache["positions"] = enriched
            _holdings_cache["analyzed"] = True
        except Exception as e:
            print(f"[holdings] sell signal analysis error: {e}")

    background_tasks.add_task(_run)
    return {"status": "started"}


# ── Budget / Position Sizing ──────────────────────────────────────────────────

@app.get("/api/budget")
def get_budget():
    from src.analysis.position_sizer import build_allocation_summary

    positions = _holdings_cache.get("positions", [])
    candidates = (_scan_cache.get("sp500") or {}).get("candidates", [])

    # Try real account; fall back to demo values
    try:
        from src.trader.alpaca_trader import get_account as _get_account
        acct = _get_account()
        portfolio_value = float(acct.portfolio_value)
        cash = float(acct.cash)
    except Exception:
        portfolio_value = 100_000.0
        cash = 100_000.0 - sum(p.get("market_value", 0) for p in positions)

    return build_allocation_summary(portfolio_value, cash, positions, candidates[:5])


@app.get("/api/budget/size/{symbol}")
def get_position_size(symbol: str, stop_loss: float = 0):
    from src.monitor.price_monitor import get_quote as _get_quote
    from src.analysis.position_sizer import size_position

    try:
        q = _get_quote(symbol.upper())
        price = q["price"]
        sl = stop_loss if stop_loss > 0 else price * 0.97
        try:
            from src.trader.alpaca_trader import get_account as _get_acct
            portfolio_value = float(_get_acct().portfolio_value)
        except Exception:
            portfolio_value = 100_000.0
        return {"symbol": symbol.upper(), "price": price, "stop_loss": sl,
                **size_position(portfolio_value, price, sl)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


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
                "qty": float(o.qty) if o.qty else None,
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


# ── Trading ───────────────────────────────────────────────────────────────────

class TradeRequest(BaseModel):
    symbol: str
    side: str                             # "buy" | "sell"
    qty: Optional[float] = None           # number of shares
    notional: Optional[float] = None      # dollar amount (alternative to qty)
    order_type: str = "market"            # "market" | "limit" | "stop" | "stop_limit"
    limit_price: Optional[float] = None
    stop_price: Optional[float] = None


MIN_CASH_PCT = 0.05  # keep ≥5% of portfolio value as cash at all times

@app.post("/api/trade")
def place_trade(req: TradeRequest):
    from src.trader.alpaca_trader import place_order, get_account
    if req.qty is None and req.notional is None:
        raise HTTPException(status_code=400, detail="Provide either qty or notional.")
    if req.side not in ("buy", "sell"):
        raise HTTPException(status_code=400, detail="side must be 'buy' or 'sell'.")

    # ── Cash reserve guard (buys only) ──────────────────────────────────────
    if req.side == "buy":
        try:
            acct = get_account()
            cash = float(acct.cash)
            portfolio_value = float(acct.portfolio_value)
            min_reserve = portfolio_value * MIN_CASH_PCT
            cost = req.notional or 0
            if req.qty and not req.notional:
                # estimate cost from qty (rough; actual fill price may differ)
                import yfinance as yf
                try:
                    cost = (yf.Ticker(req.symbol).fast_info.last_price or 0) * req.qty
                except Exception:
                    cost = 0
            if cash - cost < min_reserve:
                spendable = max(0, cash - min_reserve)
                raise HTTPException(
                    status_code=400,
                    detail=f"现金不足：下单后现金将低于 {MIN_CASH_PCT*100:.0f}% 储备金 (${min_reserve:,.0f})。"
                           f"当前可用 ${spendable:,.0f}，本次需要 ${cost:,.0f}。"
                )
        except HTTPException:
            raise
        except Exception:
            pass  # if check fails, let the order through rather than block

    try:
        order = place_order(
            symbol=req.symbol.upper(),
            side=req.side,
            qty=req.qty,
            notional=req.notional,
            order_type=req.order_type,
            limit_price=req.limit_price,
            stop_price=req.stop_price,
        )
        return {
            "id": order.id,
            "symbol": order.symbol,
            "side": order.side,
            "qty": order.qty,
            "notional": getattr(order, "notional", None),
            "type": order.type,
            "status": order.status,
            "created_at": str(order.created_at),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/api/positions/{symbol}")
def close_position_endpoint(symbol: str, background_tasks: BackgroundTasks):
    from src.trader.alpaca_trader import close_position
    try:
        order = close_position(symbol.upper())
        background_tasks.add_task(_refresh_holdings)
        return {"status": "submitted", "symbol": symbol.upper(), "order_id": order.id}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/api/orders/{order_id}")
def cancel_order_endpoint(order_id: str):
    from src.trader.alpaca_trader import cancel_order
    try:
        cancel_order(order_id)
        return {"status": "cancelled", "order_id": order_id}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Trade Agent ───────────────────────────────────────────────────────────────

@app.get("/api/agent/pending")
def get_pending_trades():
    from src.trader.trade_agent import get_pending_trades, get_agent_log
    return {
        "trades": get_pending_trades(),
        "log": get_agent_log(),
    }


SCAN_MAX_AGE_HOURS = 6   # auto-rescan if data is older than this

# ── Shared agent runner (used by cascade + manual + scheduler) ────────────────
def _run_agent_internal():
    """Core agent logic — called by cascade, manual endpoint, and scheduler."""
    from datetime import datetime
    from src.trader.trade_agent import run_agent as _run_agent
    from src.analysis.market_context import load_market_context

    # Auto-scan if missing or stale
    scan_data  = _scan_cache.get("sp500", {})
    scanned_at = scan_data.get("scanned_at")
    needs_scan = True
    age_hours  = 0
    if scanned_at:
        try:
            age_hours  = (datetime.utcnow() - datetime.fromisoformat(scanned_at)).total_seconds() / 3600
            needs_scan = age_hours > SCAN_MAX_AGE_HOURS
        except Exception:
            needs_scan = True

    if needs_scan:
        print(f"[agent] Scan {'missing' if not scanned_at else f'stale ({age_hours:.1f}h)'} — scanning first…")
        _run_sp500_scan(cascade_agent=False)   # no re-cascade
    else:
        print(f"[agent] Using cached scan (age={age_hours:.1f}h)")

    portfolio_value = 100_000.0
    try:
        from src.trader.alpaca_trader import get_account as _get_acct
        portfolio_value = float(_get_acct().portfolio_value)
    except Exception:
        pass

    ctx = load_market_context()
    _run_agent(
        scan_cache=_scan_cache,
        holdings_cache=_holdings_cache,
        watchlist=load_watchlist(),
        portfolio_value=portfolio_value,
        analysis_cache=_analysis_cache,
        analysis_timestamps=_analysis_timestamps,
        min_ai_score_override=ctx.get("min_ai_score"),
        size_scale_override=ctx.get("size_scale"),
    )
    _refresh_holdings()


@app.post("/api/agent/run")
def run_agent(background_tasks: BackgroundTasks):
    """Manual override trigger — normally the pipeline runs automatically."""
    background_tasks.add_task(_run_agent_internal)
    return {"status": "started"}


# ── Auto-approve config ───────────────────────────────────────────────────────

@app.get("/api/agent/auto-approve")
def get_auto_approve():
    """Return current auto-approve config."""
    from src.trader.trade_agent import get_auto_approve_config
    return get_auto_approve_config()


class AutoApproveRequest(BaseModel):
    enabled: bool
    threshold: Optional[float] = 0.80

@app.post("/api/agent/auto-approve")
def set_auto_approve(req: AutoApproveRequest):
    """Enable or disable auto-approve with a confidence threshold."""
    from src.trader.trade_agent import set_auto_approve as _set
    return _set(req.enabled, req.threshold or 0.80)


@app.get("/api/market/regime")
def get_regime():
    """Current market regime (BULL/NEUTRAL/CAUTION/BEAR) based on SPY."""
    from src.monitor.market_regime import get_market_regime
    try:
        return get_market_regime()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/agent/sync-fills")
def trigger_fill_sync():
    """Manually trigger order fill status sync with Alpaca."""
    from src.trader.trade_agent import sync_fills
    try:
        changed = sync_fills()
        return {"status": "ok", "updated": len(changed), "trade_ids": changed}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/agent/pending/{trade_id}/approve")
def approve_trade(trade_id: str, background_tasks: BackgroundTasks):
    from src.trader.trade_agent import approve_trade as _approve
    try:
        trade = _approve(trade_id)
        background_tasks.add_task(_refresh_holdings)
        return trade
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/agent/pending/{trade_id}/reject")
def reject_trade(trade_id: str):
    from src.trader.trade_agent import reject_trade as _reject
    try:
        return _reject(trade_id)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


# ── Portfolio Seeder (one-time Robinhood import) ──────────────────────────────

class SeedPosition(BaseModel):
    symbol: str
    qty: float

class SeedRequest(BaseModel):
    positions: list[SeedPosition]

@app.post("/api/seed-portfolio")
def seed_portfolio(req: SeedRequest):
    """
    Place market buy orders for a list of positions in the Alpaca paper account.
    Skips symbols already held. Returns summary of placed / skipped / failed orders.
    """
    import time as _time
    from src.trader.alpaca_trader import get_client, get_account
    from src.monitor.price_monitor import get_quote

    api = get_client()
    acct = get_account()
    existing = {p.symbol for p in api.list_positions()}

    placed, skipped, failed = [], [], []

    for pos in req.positions:
        symbol = pos.symbol.upper()
        qty = pos.qty

        if symbol in existing:
            skipped.append({"symbol": symbol, "reason": "already held"})
            continue

        try:
            q = get_quote(symbol)
            price = q["price"]
            order = api.submit_order(
                symbol=symbol,
                qty=qty,
                side="buy",
                type="market",
                time_in_force="gtc",
            )
            placed.append({"symbol": symbol, "qty": qty, "price": price, "order_id": order.id})
            _time.sleep(0.2)
        except Exception as e:
            failed.append({"symbol": symbol, "error": str(e)})

    # Refresh holdings cache
    try:
        from src.monitor.holdings_monitor import get_paper_positions
        _holdings_cache["positions"] = get_paper_positions()
    except Exception:
        pass

    return {
        "status": "ok",
        "placed": placed,
        "skipped": skipped,
        "failed": failed,
        "account_equity": float(acct.equity),
        "account_cash": float(acct.cash),
    }


# ── Circuit Breaker ───────────────────────────────────────────────────────────

@app.get("/api/circuit-breaker")
def get_circuit_breaker():
    """Return current portfolio circuit breaker state."""
    from src.monitor.circuit_breaker import get_circuit_breaker_state
    return get_circuit_breaker_state()


@app.post("/api/circuit-breaker/reset")
def reset_circuit_breaker():
    """Manually reset the circuit breaker (re-enable buys)."""
    from src.monitor.circuit_breaker import reset_breaker
    return reset_breaker()


# ── Portfolio History ─────────────────────────────────────────────────────────

@app.get("/api/portfolio/history")
def get_portfolio_history():
    from src.monitor.portfolio_history import get_history
    try:
        return get_history()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Backtesting ───────────────────────────────────────────────────────────────

_backtest_cache: dict = {}
_backtest_running: bool = False

# ── Review cache with disk persistence ───────────────────────────────────────
_REVIEW_CACHE_FILE = Path(__file__).parent.parent / "data" / "review_cache.json"

def _load_review_cache() -> dict:
    try:
        if _REVIEW_CACHE_FILE.exists():
            return json.loads(_REVIEW_CACHE_FILE.read_text())
    except Exception:
        pass
    return {}

def _save_review_cache(cache: dict):
    try:
        _REVIEW_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        _REVIEW_CACHE_FILE.write_text(json.dumps(cache, default=str))
    except Exception as e:
        print(f"[review] cache save error: {e}")

_review_cache: dict = _load_review_cache()     # date -> review dict


@app.get("/api/backtest")
def get_backtest():
    return _backtest_cache.get("result", {"status": "not_run"})


@app.post("/api/backtest")
def trigger_backtest(
    background_tasks: BackgroundTasks,
    symbols: str = "",
    hold_days: int = 10,
    target_pct: float = 0.08,
    period: str = "2y",
):
    global _backtest_running
    if _backtest_running:
        return {"status": "already_running"}

    # Build symbol list: use provided, else fall back to watchlist + scan candidates
    if symbols:
        sym_list = [s.strip().upper() for s in symbols.split(",") if s.strip()]
    else:
        sym_list = load_watchlist()
        scan_candidates = (_scan_cache.get("sp500") or {}).get("candidates", [])
        sym_list += [c["symbol"] for c in scan_candidates[:10]]
        sym_list = list(dict.fromkeys(sym_list))  # deduplicate, preserve order

    def _run():
        global _backtest_running
        _backtest_running = True
        _backtest_cache["result"] = {"status": "running", "symbols": sym_list}
        try:
            from src.analysis.backtester import run_backtest
            result = run_backtest(sym_list, period=period, hold_days=hold_days, target_pct=target_pct)
            if "error" in result:
                result["status"] = "error"
            else:
                result["status"] = "done"
            result["symbols"] = sym_list
            result["params"] = {"hold_days": hold_days, "target_pct": target_pct, "period": period}
            _backtest_cache["result"] = result
        except Exception as e:
            _backtest_cache["result"] = {"status": "error", "error": str(e)}
        finally:
            _backtest_running = False

    background_tasks.add_task(_run)
    return {"status": "started", "symbols": sym_list}


# ── Scheduler: auto-trigger after market close ────────────────────────────────

def _start_scheduler():
    """Background thread that triggers strategy review at 4:15 PM ET (Mon-Fri)."""
    import time
    from datetime import datetime, timezone, timedelta

    ET = timezone(timedelta(hours=-4))   # EDT (switches to -5 in Nov, close enough)

    def _loop():
        triggered_dates: set[str] = set()
        while True:
            now_et = datetime.now(ET)
            today_str = now_et.strftime("%Y-%m-%d")
            h, m = now_et.hour, now_et.minute
            is_weekday = now_et.weekday() < 5
            after_close = (h, m) >= (16, 15)
            already_done = today_str in triggered_dates or (
                _review_cache.get("latest", {}).get("date") == today_str
            )
            if is_weekday and after_close and not already_done:
                print(f"[scheduler] Market closed — triggering strategy review for {today_str}")
                triggered_dates.add(today_str)
                try:
                    _run_strategy_review()
                except Exception as e:
                    print(f"[scheduler] Review error: {e}")
            time.sleep(60)

    t = threading.Thread(target=_loop, daemon=True, name="review-scheduler")
    t.start()
    print("[scheduler] Market-close review scheduler started (checks every 60s, fires at 4:15 PM ET)")


# ── Strategy Review ──────────────────────────────────────────────────────────

def _run_strategy_review():
    """Generate end-of-day strategy review and optionally email it."""
    from datetime import date
    from src.analysis.strategy_reviewer import generate_strategy_review
    from src.monitor.portfolio_history import get_history
    from src.trader.trade_agent import get_pending_trades, get_agent_log

    today = date.today().isoformat()
    print(f"[review] Generating strategy review for {today}…")
    _review_cache["status"] = "running"
    try:
        history = get_history()
        agent_state_trades = get_pending_trades()   # includes all statuses
        agent_log   = get_agent_log()
        scan_result = _scan_cache.get("sp500", {})

        # Today's executed Alpaca orders
        executed_orders: list = []
        try:
            from src.trader.alpaca_trader import get_client
            alpaca = get_client()
            raw_orders = alpaca.list_orders(status="closed", limit=50)
            executed_orders = [
                {
                    "symbol": o.symbol,
                    "side": o.side,
                    "qty": float(o.qty),
                    "filled_qty": float(o.filled_qty) if o.filled_qty else 0,
                    "filled_avg_price": float(o.filled_avg_price) if o.filled_avg_price else None,
                    "status": o.status,
                }
                for o in raw_orders
                if str(o.created_at)[:10] == today
            ]
        except Exception as e:
            print(f"[review] Could not fetch orders: {e}")

        backtest_data = _backtest_cache.get("result", {})
        review = generate_strategy_review(
            portfolio_history=history,
            executed_orders=executed_orders,
            agent_log=agent_log,
            agent_trades=agent_state_trades,
            scan_result=scan_result,
            backtest_result=backtest_data if backtest_data.get("status") == "done" else None,
        )

        _review_cache[today] = review
        _review_cache["latest"] = review
        _review_cache["status"] = "done"
        _save_review_cache({k: v for k, v in _review_cache.items() if k not in {"status", "error"}})
        print(f"[review] Done: {review.get('one_line_summary','')}")


    except Exception as e:
        _review_cache["status"] = "error"
        _review_cache["error"] = str(e)
        print(f"[review] Error: {e}")


@app.get("/api/strategy/review")
def get_strategy_review():
    from datetime import date
    today = date.today().isoformat()
    review = _review_cache.get(today) or _review_cache.get("latest")
    if not review:
        status = _review_cache.get("status")
        if status == "running":
            return {"status": "running"}
        raise HTTPException(status_code=404, detail="No review yet. POST /api/strategy/review to generate.")

    # Inject live performance data so it's always current, not frozen at generation time
    try:
        from src.trader.alpaca_trader import get_account
        from src.monitor.portfolio_history import get_history
        acct = get_account()
        live_equity = float(acct.equity)
        hist = get_history()
        days = hist.get("days", [])
        # yesterday's closing equity
        yesterday_equity = next(
            (d["equity"] for d in reversed(days) if d["date"] < today),
            100_000.0
        )
        daily_pl = round(live_equity - yesterday_equity, 2)
        daily_return_pct = round(daily_pl / yesterday_equity * 100, 3) if yesterday_equity else 0
        # monthly: from first day of month
        from datetime import date as _date
        month_start = _date.today().replace(day=1).isoformat()
        month_start_equity = next(
            (d["equity"] for d in days if d["date"] >= month_start),
            yesterday_equity
        )
        monthly_pl_pct = round((live_equity - month_start_equity) / month_start_equity * 100, 3) if month_start_equity else 0
        target = review.get("performance", {}).get("target_monthly_pct", 10.0)
        review = dict(review)
        review["performance"] = {
            **review.get("performance", {}),
            "current_equity": round(live_equity, 2),
            "daily_pl": daily_pl,
            "daily_return_pct": daily_return_pct,
            "monthly_return_pct": monthly_pl_pct,
            "target_monthly_pct": target,
            "target_gap": round(target - monthly_pl_pct, 3),
        }
    except Exception:
        pass  # if live fetch fails, return cached performance as-is

    return review


@app.get("/api/strategy/reviews")
def get_all_reviews():
    """Return all cached daily reviews (chronological)."""
    skip = {"status", "error", "latest"}
    reviews = [v for k, v in _review_cache.items() if k not in skip and isinstance(v, dict) and "date" in v]
    return sorted(reviews, key=lambda r: r["date"], reverse=True)


@app.post("/api/strategy/review")
def trigger_strategy_review(background_tasks: BackgroundTasks):
    if _review_cache.get("status") == "running":
        return {"status": "already_running"}
    background_tasks.add_task(_run_strategy_review)
    return {"status": "started"}


_OVERRIDES_FILE         = Path(__file__).parent.parent / "data" / "strategy_overrides.json"
_OVERRIDES_HISTORY_FILE = Path(__file__).parent.parent / "data" / "strategy_overrides_history.json"
_NOTES_FILE             = Path(__file__).parent.parent / "data" / "strategy_notes.json"


def _load_notes() -> list:
    try:
        if _NOTES_FILE.exists():
            return json.loads(_NOTES_FILE.read_text())
    except Exception:
        pass
    return []

def _save_notes(notes: list):
    _NOTES_FILE.parent.mkdir(exist_ok=True)
    _NOTES_FILE.write_text(json.dumps(notes, indent=2))

def _load_overrides_history() -> list:
    try:
        if _OVERRIDES_HISTORY_FILE.exists():
            return json.loads(_OVERRIDES_HISTORY_FILE.read_text())
    except Exception:
        pass
    return []

def _append_overrides_history(before: dict, after: dict, reason: str, source_review_date: str | None = None):
    history = _load_overrides_history()
    from datetime import datetime as _dt
    history.append({
        "changed_at": _dt.utcnow().isoformat(),
        "source_review_date": source_review_date,
        "reason": reason,
        "before": {k: before.get(k) for k in ("risk_pct", "max_position_pct", "min_ai_score", "stop_loss_pct")},
        "after":  {k: after.get(k)  for k in ("risk_pct", "max_position_pct", "min_ai_score", "stop_loss_pct")},
    })
    _OVERRIDES_HISTORY_FILE.parent.mkdir(exist_ok=True)
    _OVERRIDES_HISTORY_FILE.write_text(json.dumps(history[-50:], indent=2))  # keep last 50


def _load_overrides() -> dict:
    try:
        if _OVERRIDES_FILE.exists():
            return json.loads(_OVERRIDES_FILE.read_text())
    except Exception:
        pass
    return {}


@app.get("/api/strategy/overrides")
def get_strategy_overrides():
    """Return current agent parameter overrides (adopted iteration changes)."""
    from src.analysis.position_sizer import DEFAULT_RISK_PCT, DEFAULT_MAX_PCT
    overrides = _load_overrides()
    return {
        "risk_pct":          overrides.get("risk_pct",          DEFAULT_RISK_PCT),
        "max_position_pct":  overrides.get("max_position_pct",  DEFAULT_MAX_PCT),
        "min_ai_score":      overrides.get("min_ai_score",       None),
        "stop_loss_pct":     overrides.get("stop_loss_pct",      0.03),
        "updated_at":        overrides.get("updated_at"),
        "reason":            overrides.get("reason"),
    }


class OverridesRequest(BaseModel):
    risk_pct:           Optional[float] = None
    max_position_pct:   Optional[float] = None
    min_ai_score:       Optional[float] = None
    stop_loss_pct:      Optional[float] = None
    reason:             Optional[str]   = None
    source_review_date: Optional[str]   = None


@app.post("/api/strategy/overrides")
def save_strategy_overrides(req: OverridesRequest):
    """Save adopted parameter overrides to disk. Agents read these on next run."""
    from datetime import datetime as _dt
    existing = _load_overrides()
    patch = {k: v for k, v in req.dict().items() if v is not None and k not in ("reason", "source_review_date")}
    updated = {**existing, **patch, "reason": req.reason, "updated_at": _dt.utcnow().isoformat()}
    try:
        _OVERRIDES_FILE.parent.mkdir(exist_ok=True)
        _OVERRIDES_FILE.write_text(json.dumps(updated, indent=2))
        _append_overrides_history(existing, updated, req.reason or "", req.source_review_date)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Could not save overrides: {e}")
    return updated


@app.get("/api/strategy/overrides/history")
def get_overrides_history():
    """Return version history of all override changes."""
    return _load_overrides_history()


# ── Strategy Notes ─────────────────────────────────────────────────────────────

class NoteRequest(BaseModel):
    text: str
    source_review_date: Optional[str] = None


@app.get("/api/strategy/notes")
def get_strategy_notes():
    return _load_notes()


@app.post("/api/strategy/notes")
def add_strategy_note(req: NoteRequest):
    import uuid as _uuid
    from datetime import datetime as _dt
    notes = _load_notes()
    note = {
        "id": str(_uuid.uuid4())[:8],
        "text": req.text,
        "source_review_date": req.source_review_date,
        "created_at": _dt.utcnow().isoformat(),
        "active": True,
    }
    notes.append(note)
    _save_notes(notes)
    return note


@app.delete("/api/strategy/notes/{note_id}")
def delete_strategy_note(note_id: str):
    notes = _load_notes()
    notes = [n for n in notes if n.get("id") != note_id]
    _save_notes(notes)
    return {"status": "deleted"}


@app.post("/api/strategy/param-extract")
async def extract_strategy_params(body: dict):
    """
    Given a strategy iteration description, ask Claude to map it to concrete
    parameter changes relative to current values. Returns list of param changes
    for user confirmation before applying.
    """
    import re
    import json as _json
    import anthropic
    from src.config import get_anthropic_key
    from src.analysis.position_sizer import DEFAULT_RISK_PCT, DEFAULT_MAX_PCT

    title       = body.get("title", "")
    description = body.get("description", "")
    expected    = body.get("expected_impact", "")

    # Read current overrides (or defaults)
    ov = _load_overrides()
    cur_risk     = ov.get("risk_pct",         DEFAULT_RISK_PCT)
    cur_max_pos  = ov.get("max_position_pct",  DEFAULT_MAX_PCT)
    cur_min_ai   = ov.get("min_ai_score",      None)   # None = regime-determined
    cur_sl_pct   = ov.get("stop_loss_pct",     0.03)

    client = anthropic.Anthropic(api_key=get_anthropic_key())
    prompt = f"""You are a quant trading system that maps natural-language strategy suggestions to concrete parameter changes.

Current agent parameters:
- risk_pct: {cur_risk*100:.2f}% (fraction of portfolio risked per trade)
- max_position_pct: {cur_max_pos*100:.0f}% (max single-position size as % of portfolio)
- min_ai_score: {f"{cur_min_ai}" if cur_min_ai is not None else "regime-based (typically 7.0)"}  (minimum AI score 0-10 to queue a trade)
- stop_loss_pct: {cur_sl_pct*100:.1f}% (default stop-loss distance from entry price)

Proposed strategy iteration:
Title: {title}
Description: {description}
Expected Impact: {expected}

Task: Determine if this iteration maps to a change in the numeric parameters listed above.
If yes, return the specific before/after values for each affected parameter.
If the iteration is qualitative (e.g. "focus on momentum stocks") and does NOT map to these parameters, set mappable=false.

Return ONLY valid JSON:
{{
  "mappable": true or false,
  "note": "brief explanation of what is being changed, or why it doesn't map",
  "params": [
    {{
      "name": "risk_pct|max_position_pct|min_ai_score|stop_loss_pct",
      "label": "human-readable name in Chinese",
      "current": <current value as number>,
      "proposed": <proposed value as number>,
      "unit": "% or score or other unit label",
      "display_current": "e.g. 2.0%",
      "display_proposed": "e.g. 1.5%"
    }}
  ]
}}

Only include params that actually change. If mappable=false, params=[]."""

    msg = anthropic.Anthropic(api_key=get_anthropic_key()).messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=512,
        messages=[{"role": "user", "content": prompt}],
    )
    text = msg.content[0].text
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        # fallback: non-mappable
        return {"mappable": False, "note": "无法自动识别参数变更，请手动调整。", "params": []}
    return _json.loads(match.group())


@app.post("/api/strategy/debate")
async def debate_iteration(body: dict):
    """3-agent debate: Trading Agent vs Backtest Agent, reviewed by Strategy Agent."""
    import re
    import json as _json
    import anthropic
    from src.config import get_anthropic_key

    title           = body.get("title", "")
    description     = body.get("description", "")
    priority        = body.get("priority", "MEDIUM")
    expected_impact = body.get("expected_impact", "")

    # Pull recent backtest stats if available for grounding
    bt_ctx = ""
    bt = _review_cache.get("latest", {})
    if bt:
        perf = bt.get("performance", {})
        bt_ctx = (
            f"Recent performance: monthly {perf.get('monthly_return_pct', 0):+.1f}% "
            f"(target {perf.get('target_monthly_pct', 10)}%), "
            f"gap {perf.get('target_gap', 0):+.1f}%"
        )

    client = anthropic.Anthropic(api_key=get_anthropic_key())
    prompt = f"""You are running a 3-agent strategy debate panel for a trading system.

Context: {bt_ctx or 'No recent performance data.'}

Proposed strategy change:
Title: {title}
Priority: {priority}
Description: {description}
Expected Impact: {expected_impact}

The three agents each respond from their perspective:

1. **交易 Agent (Rex)** — Live signal expert. Focused on current market conditions, signal quality, and execution feasibility. Argues based on what's happening NOW in the market.

2. **回测 Agent** — Historical data expert. Argues based on what the numbers show: win rates, drawdowns, statistical significance. Skeptical of changes without backtest evidence.

3. **复盘 Agent** — Strategic synthesizer. Weighs both views, flags risks, considers the 15%/month target. Makes the final call.

Return ONLY valid JSON:
{{
  "trading_agent": "Rex's take (2 sentences, signal/execution focused)",
  "backtest_agent": "Backtest Agent's take (2 sentences, data/evidence focused)",
  "review_agent": "Strategy Agent synthesis (2 sentences, balanced + decisive)",
  "recommendation": "ADOPT" | "HOLD" | "REJECT",
  "confidence": 0.0-1.0
}}"""

    msg = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1024,
        temperature=0,
        messages=[{"role": "user", "content": prompt}],
    )
    text = msg.content[0].text
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise HTTPException(status_code=500, detail="Debate parse failed")
    result = _json.loads(match.group())
    # Keep backward-compat fields for existing frontend
    result["pro"]      = result.get("trading_agent", "")
    result["con"]      = result.get("backtest_agent", "")
    result["synthesis"] = result.get("review_agent", "")
    return result


@app.post("/api/strategy/debate/stock")
async def debate_stock(body: dict):
    """3-agent debate on whether to buy/hold/sell a specific stock."""
    import re
    import json as _json
    import anthropic
    from src.config import get_anthropic_key

    symbol   = body.get("symbol", "").upper()
    action   = body.get("action", "BUY")   # BUY | HOLD | SELL
    context  = body.get("context", {})     # price, pnl, technicals, scan data

    price       = context.get("price", 0)
    pnl_pct     = context.get("unrealized_plpc", context.get("pnl_pct", 0))
    rsi         = context.get("rsi", "N/A")
    mom5        = context.get("mom5d_pct", context.get("mom5", "N/A"))
    vs_ma20     = context.get("vs_ma20_pct", "N/A")
    signal      = context.get("signal", "N/A")
    ai_score    = context.get("ai_score", "N/A")
    reason      = context.get("reason", "")

    stock_ctx = (
        f"{symbol} @ ${price} | P&L: {pnl_pct:+.1f}% | "
        f"RSI={rsi} | 5d_mom={mom5:+.1f}% | vs_MA20={vs_ma20} | "
        f"Signal={signal} score={ai_score}"
    ) if price else f"{symbol} (no live data)"

    client = anthropic.Anthropic(api_key=get_anthropic_key())
    prompt = f"""3-agent panel debating a {action} decision for {symbol}.

Stock data: {stock_ctx}
Analyst note: {reason[:200] if reason else 'N/A'}

1. **交易 Agent (Rex)** — Signal execution perspective. Is the technical setup right? Timing?
2. **回测 Agent** — Historical pattern perspective. Does this setup have a positive edge historically?
3. **复盘 Agent** — Risk/portfolio perspective. Position sizing, risk/reward, portfolio context.

Return ONLY valid JSON:
{{
  "trading_agent": "Rex's view on the {action} decision (2 sentences)",
  "backtest_agent": "Historical edge assessment (2 sentences)",
  "review_agent": "Risk/portfolio synthesis (2 sentences)",
  "verdict": "STRONG_{action}" | "{action}" | "HOLD" | "AVOID",
  "confidence": 0.0-1.0,
  "key_risk": "The single biggest risk in one sentence"
}}"""

    msg = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=800,
        temperature=0,
        messages=[{"role": "user", "content": prompt}],
    )
    text = msg.content[0].text
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise HTTPException(status_code=500, detail="Stock debate parse failed")
    return _json.loads(match.group())


# ── Serve built React app ─────────────────────────────────────────────────────

frontend_dist = Path(__file__).parent.parent / "frontend" / "dist"
if frontend_dist.exists():
    app.mount("/", StaticFiles(directory=str(frontend_dist), html=True), name="static")
