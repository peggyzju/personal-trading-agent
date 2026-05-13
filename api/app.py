from __future__ import annotations
import json
import os
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, BackgroundTasks
from pydantic import BaseModel
from typing import Optional
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

load_dotenv()

app = FastAPI(title="Personal Trading Agent")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:5174"],
    allow_methods=["*"],
    allow_headers=["*"],
)

WATCHLIST_FILE = Path(__file__).parent.parent / "watchlist.json"
DEFAULT_WATCHLIST = ["AAPL", "NVDA", "MSFT", "TSLA"]

_analysis_cache: dict = {}           # symbol -> analysis dict
_analysis_timestamps: dict = {}      # symbol -> unix timestamp of last update
_news_cache: dict = {}
_brief_cache: dict = {}
_scan_cache: dict = {}               # "sp500" -> {candidates, scanned_at, status}
_holdings_cache: dict = {}           # "sell_signals" -> list, "positions" -> list
_scan_running: bool = False


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

def _run_sp500_scan():
    global _scan_running
    from datetime import datetime
    from src.monitor.sp500_scanner import get_scan_universe, quick_screen
    from src.analysis.stock_screener import ai_score_candidates

    _scan_running = True
    _scan_cache["sp500"] = {"status": "running", "candidates": [], "scanned_at": None}
    try:
        print("[scan] Building scan universe (S&P 500 + NASDAQ-100 + Layer 2)…")
        tickers = get_scan_universe()
        print(f"[scan] Quick-screening {len(tickers)} tickers…")
        top_tech = quick_screen(tickers, top_n=25)
        print(f"[scan] {len(top_tech)} passed technical filter. Running AI scoring…")
        top_ai = ai_score_candidates(top_tech)
        _scan_cache["sp500"] = {
            "status": "done",
            "candidates": top_ai,
            "scanned_at": datetime.utcnow().isoformat(),
            "total_screened": len(tickers),
            "tech_passed": len(top_tech),
        }
        print(f"[scan] Done. Top candidate: {top_ai[0]['symbol'] if top_ai else 'none'}")
    except Exception as e:
        _scan_cache["sp500"] = {"status": "error", "error": str(e), "candidates": []}
    finally:
        _scan_running = False


@app.get("/api/scan/sp500")
def get_scan():
    return _scan_cache.get("sp500", {"status": "not_run", "candidates": []})


@app.post("/api/scan/sp500")
def trigger_scan(background_tasks: BackgroundTasks):
    global _scan_running
    if _scan_running:
        return {"status": "already_running"}
    background_tasks.add_task(_run_sp500_scan)
    return {"status": "started"}


# ── Holdings Monitor ──────────────────────────────────────────────────────────

def _refresh_holdings():
    """Background task: re-fetch positions + sell signals after any trade action."""
    from src.monitor.holdings_monitor import get_paper_positions, analyze_sell_signals
    positions = get_paper_positions()
    _holdings_cache["positions"] = positions
    _holdings_cache["analyzed"] = False
    try:
        enriched = analyze_sell_signals(positions)
        _holdings_cache["positions"] = enriched
        _holdings_cache["analyzed"] = True
    except Exception as e:
        print(f"[holdings] auto-refresh error: {e}")


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


# ── Trading ───────────────────────────────────────────────────────────────────

class TradeRequest(BaseModel):
    symbol: str
    side: str                             # "buy" | "sell"
    qty: Optional[float] = None           # number of shares
    notional: Optional[float] = None      # dollar amount (alternative to qty)
    order_type: str = "market"            # "market" | "limit" | "stop" | "stop_limit"
    limit_price: Optional[float] = None
    stop_price: Optional[float] = None


@app.post("/api/trade")
def place_trade(req: TradeRequest):
    from src.trader.alpaca_trader import place_order
    if req.qty is None and req.notional is None:
        raise HTTPException(status_code=400, detail="Provide either qty or notional.")
    if req.side not in ("buy", "sell"):
        raise HTTPException(status_code=400, detail="side must be 'buy' or 'sell'.")
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

@app.post("/api/agent/run")
def run_agent(background_tasks: BackgroundTasks):
    """Manually trigger the signal engine. Auto-runs scan first if data is missing or stale (>4h)."""
    def _run():
        from datetime import datetime

        # ── Auto-scan if missing or stale ────────────────────────────────────
        scan_data = _scan_cache.get("sp500", {})
        scanned_at = scan_data.get("scanned_at")
        needs_scan = True
        if scanned_at:
            try:
                age_hours = (datetime.utcnow() - datetime.fromisoformat(scanned_at)).total_seconds() / 3600
                needs_scan = age_hours > SCAN_MAX_AGE_HOURS
            except Exception:
                needs_scan = True

        if needs_scan:
            print(f"[agent] Scan data {'missing' if not scanned_at else 'stale (>{:.1f}h)'.format(age_hours if scanned_at else 0)} — running scan first…")
            _run_sp500_scan()
        else:
            print(f"[agent] Using cached scan data (age={age_hours:.1f}h < {SCAN_MAX_AGE_HOURS}h)")

        # ── Run agent ─────────────────────────────────────────────────────────
        from src.trader.trade_agent import run_agent as _run_agent
        portfolio_value = 100_000.0
        try:
            from src.trader.alpaca_trader import get_account as _get_acct
            portfolio_value = float(_get_acct().portfolio_value)
        except Exception:
            pass
        _run_agent(
            scan_cache=_scan_cache,
            holdings_cache=_holdings_cache,
            watchlist=load_watchlist(),
            portfolio_value=portfolio_value,
            analysis_cache=_analysis_cache,
            analysis_timestamps=_analysis_timestamps,
        )

    background_tasks.add_task(_run)
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
_review_cache: dict = {}     # date -> review dict


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

        review = generate_strategy_review(
            portfolio_history=history,
            executed_orders=executed_orders,
            agent_log=agent_log,
            agent_trades=agent_state_trades,
            scan_result=scan_result,
        )

        _review_cache[today] = review
        _review_cache["latest"] = review
        _review_cache["status"] = "done"
        print(f"[review] Done: {review.get('one_line_summary','')}")


    except Exception as e:
        _review_cache["status"] = "error"
        _review_cache["error"] = str(e)
        print(f"[review] Error: {e}")


@app.get("/api/strategy/review")
def get_strategy_review():
    from datetime import date
    today = date.today().isoformat()
    if today in _review_cache:
        return _review_cache[today]
    if _review_cache.get("latest"):
        return _review_cache["latest"]
    status = _review_cache.get("status")
    if status == "running":
        return {"status": "running"}
    raise HTTPException(status_code=404, detail="No review yet. POST /api/strategy/review to generate.")


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


# ── Serve built React app ─────────────────────────────────────────────────────

frontend_dist = Path(__file__).parent.parent / "frontend" / "dist"
if frontend_dist.exists():
    app.mount("/", StaticFiles(directory=str(frontend_dist), html=True), name="static")
