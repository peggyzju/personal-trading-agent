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
    threading.Thread(target=_refresh_holdings, daemon=True).start()  # non-blocking pre-warm
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
_last_cascade_at: float = 0.0        # unix timestamp of last cascade-to-agent


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


def load_backtest_universe(n: int = 150) -> list[str]:
    """
    Return a liquid backtest universe: S&P 500 top-N (by Wikipedia order, which
    roughly tracks market cap) + Nasdaq-100 growth stocks + watchlist.
    Deduped, capped at n symbols so download stays fast (~15-20s).
    """
    from src.monitor.sp500_scanner import get_sp500_tickers, get_nasdaq100_tickers
    sp500  = get_sp500_tickers()          # ~500, roughly market-cap ordered
    ndq100 = get_nasdaq100_tickers()      # ~100, growth/tech heavy
    wl     = load_watchlist()

    seen: set[str] = set()
    universe: list[str] = []
    for sym in sp500[:n] + ndq100 + wl:  # sp500 first so top-cap lead
        if sym not in seen:
            seen.add(sym)
            universe.append(sym)
        if len(universe) >= n:
            break
    return universe


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

_ANALYSIS_TTL_SECS = 2 * 3600   # 2 hours — don't re-call Claude if cache is fresh

@app.post("/api/analyze/{symbol}")
def run_analysis(symbol: str, force: bool = False):
    from src.monitor.price_monitor import get_quote as _get_quote, get_ohlcv
    from src.monitor.news_monitor import get_news
    from src.analysis.ai_analyst import analyze
    import time as _time

    s = symbol.upper()
    try:
        quote = _get_quote(s)

        # ── Cache check: skip Claude if analysis is fresh and price hasn't moved >1.5% ──
        if not force and s in _analysis_cache and s in _analysis_timestamps:
            age  = _time.time() - _analysis_timestamps[s]
            cached_price = _analysis_cache[s].get("price") or 0
            live_price   = quote.get("price") or 0
            price_moved  = abs(live_price - cached_price) / cached_price * 100 if cached_price else 100
            if age < _ANALYSIS_TTL_SECS and price_moved < 1.5:
                cached = dict(_analysis_cache[s])
                cached["cached"] = True
                cached["cache_age_min"] = round(age / 60, 0)
                print(f"[analyze] {s} cache hit (age={age/60:.0f}min, Δprice={price_moved:.1f}%) — skipping Claude")
                return cached

        ohlcv = get_ohlcv(s)
        news = get_news(s)
        result = analyze(s, ohlcv, quote, news=news)
        result["symbol"] = s
        result["price"] = quote["price"]
        result["change_pct"] = quote["change_pct"]
        result["cached"] = False
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
    global _scan_running, _last_cascade_at

    # Guard and flag-set BEFORE slow imports to minimize the race window
    if _scan_running:
        print("[scan] Already running — skipping duplicate trigger")
        return
    _scan_running = True
    _scan_cache["sp500"] = {"status": "running", "candidates": [], "scanned_at": None, "progress": "starting"}

    import threading
    import time as _time
    from datetime import datetime
    from src.monitor.sp500_scanner import (
        get_scan_universe, get_sp500_tickers, get_nasdaq100_tickers,
        LAYER2_TICKERS, quick_screen, enrich_with_fundamentals,
    )
    from src.analysis.stock_screener import ai_score_candidates
    from src.analysis.market_context import load_market_context

    from src.monitor.sp500_scanner import SECTOR_MAP as _SCANNER_SECTOR_MAP
    BROAD_SECTOR_MAP = {
        "tech":          ["AAPL","MSFT","GOOGL","META","NVDA","AMD","AVGO","ORCL"],
        "semiconductors":["NVDA","AMD","AVGO","QCOM","INTC","MU","AMAT","KLAC","LRCX","MRVL","SOXX"],
        "healthcare":    ["UNH","JNJ","LLY","ABBV","MRK","TMO","ABT","DHR","ISRG","VRTX"],
        "energy":        ["XOM","CVX","COP","SLB","EOG","PXD","MPC","VLO","PSX"],
        "financials":    ["JPM","BAC","WFC","GS","MS","BLK","AXP","SPGI"],
        "industrials":   ["CAT","HON","UPS","BA","RTX","LMT","DE","MMM","GE"],
        "consumer":      ["AMZN","TSLA","HD","MCD","NKE","SBUX","TGT","LOW"],
        "utilities":     ["NEE","DUK","SO","AEP","EXC","SRE"],
    }

    # Watchdog: reset _scan_running if scan hangs for >12 minutes
    def _watchdog():
        global _scan_running
        _scan_running = False
        print("[scan] watchdog: scan took >12 min, reset _scan_running")

    watchdog = threading.Timer(720, _watchdog)
    watchdog.daemon = True
    watchdog.start()

    def _set_progress(step: str):
        if isinstance(_scan_cache.get("sp500"), dict):
            _scan_cache["sp500"]["progress"] = step

    try:
        _set_progress("loading_market_context")
        ctx = load_market_context()
        sector_bias = ctx.get("sector_bias", {})

        symbol_bias: dict[str, str] = {}
        for sector, bias in sector_bias.items():
            if bias != "neutral":
                for sym in BROAD_SECTOR_MAP.get(sector, []):
                    if sym not in symbol_bias or bias == "negative":
                        symbol_bias[sym] = bias

        _set_progress("building_universe")
        print("[scan] Building scan universe (S&P 500 + NASDAQ-100 + Layer 2)…")
        tickers     = get_scan_universe()
        _sp500_set  = set(get_sp500_tickers())
        _nasdaq_set = set(get_nasdaq100_tickers())
        _layer2_set = set(LAYER2_TICKERS)

        _set_progress("downloading_data")
        print(f"[scan] Quick-screening {len(tickers)} tickers…")

        def _progress_cb(step: str, done: int, total: int):
            _set_progress(f"downloading ({done}/{total} chunks)")

        watchlist_set = set(load_watchlist())
        top_tech = quick_screen(tickers, top_n=25, progress_cb=_progress_cb, force_symbols=watchlist_set)

        for c in top_tech:
            sym = c["symbol"]
            c["universe"] = (
                "sp500" if sym in _sp500_set else
                "nasdaq100" if sym in _nasdaq_set else
                "layer2" if sym in _layer2_set else "other"
            )

        print(f"[scan] {len(top_tech)} passed technical filter. Enriching with fundamentals…")
        _set_progress("enriching_fundamentals")
        top_tech = enrich_with_fundamentals(top_tech)

        print(f"[scan] Fundamentals enriched. Fetching news for top candidates…")
        _set_progress("fetching_news")

        # Fetch news + earnings warnings for top 15 candidates in parallel
        from concurrent.futures import ThreadPoolExecutor as _TPE
        from src.monitor.news_monitor import get_news as _get_news, earnings_within_days as _earnings_check

        def _fetch_news_info(c: dict) -> tuple[str, dict]:
            sym = c["symbol"]
            try:
                items    = _get_news(sym, limit=4)
                headlines = [n["title"] for n in items if n.get("title")][:2]
            except Exception:
                headlines = []
            try:
                has_earn, earn_date = _earnings_check(sym, days=5)
                earn_warn = f"earnings on {earn_date}" if has_earn else None
            except Exception:
                earn_warn = None
            return sym, {"headlines": headlines, "earnings_warning": earn_warn}

        with _TPE(max_workers=8) as _pool:
            _news_results = list(_pool.map(_fetch_news_info, top_tech[:15]))
        news_map = dict(_news_results)
        news_ct  = sum(1 for v in news_map.values() if v.get("headlines"))
        earn_ct  = sum(1 for v in news_map.values() if v.get("earnings_warning"))
        print(f"[scan] News fetched: {news_ct} stocks with headlines, {earn_ct} with earnings warning")

        # Fetch WSB sentiment for Layer 2 symbols in scan results
        _set_progress("fetching_wsb")
        try:
            from src.monitor.reddit_monitor import fetch_wsb_mentions
            _wsb_syms = [c["symbol"] for c in top_tech if c.get("universe") == "layer2"]
            if _wsb_syms:
                _wsb_map = fetch_wsb_mentions(_wsb_syms)
                for sym, wsb in _wsb_map.items():
                    if sym in news_map:
                        news_map[sym]["wsb_hype"] = wsb
                    else:
                        news_map[sym] = {"headlines": [], "earnings_warning": None, "wsb_hype": wsb}
                # Embed wsb_hype into candidate dict so Rex can read it without re-fetching
                for c in top_tech:
                    if c["symbol"] in _wsb_map:
                        c["wsb_hype"] = _wsb_map[c["symbol"]]
                _wsb_extreme = [s for s, v in _wsb_map.items() if v["hype_label"] == "extreme"]
                if _wsb_extreme:
                    print(f"[scan] WSB extreme hype: {_wsb_extreme}")
        except Exception as _wsb_err:
            print(f"[scan] WSB fetch error (non-fatal): {_wsb_err}")

        print(f"[scan] Running AI scoring (with news + WSB + regime + sector context)…")
        _set_progress("ai_scoring")
        _scan_notes = [n["text"] for n in _load_notes() if n.get("active", True)]
        top_ai = _sanitize_floats(ai_score_candidates(
            top_tech,
            strategy_notes=_scan_notes or None,
            news_map=news_map,
            market_context=ctx,
            sector_bias=sector_bias,
        ))

        # Sector bias post-sort only (scoring already incorporates bias via prompt)
        if top_ai:
            top_ai = sorted(top_ai, key=lambda x: (
                0 if x.get("signal") in ("STRONG_BUY", "BUY") else 1,
                -(x.get("ai_score") or 0),
            ))

        ai_scored = sum(1 for c in top_ai if c.get("ai_score") is not None)
        _scan_cache["sp500"] = {
            "status":        "done",
            "progress":      "done",
            "candidates":    top_ai,
            "scanned_at":    datetime.utcnow().isoformat(),
            "total_screened": len(tickers),
            "tech_passed":   len(top_tech),
            "ai_scored":     ai_scored,
        }

        # ── Auto-record daily strategy snapshot ───────────────────────────────
        try:
            from src.analysis.strategy_logger import record_daily_snapshot
            from src.trader.alpaca_trader import get_client as _get_alpaca
            record_daily_snapshot(
                candidates=top_ai,
                alpaca_api=_get_alpaca(),
                extra={
                    "total_screened":     len(tickers),
                    "tech_passed":        len(top_tech),
                    "quality_gate_removed": 0,
                    "ai_scored":          ai_scored,
                    "market_regime":      ctx.get("regime"),
                },
            )
        except Exception as _log_err:
            print(f"[scan] strategy_logger error (non-fatal): {_log_err}")
        _save_scan_cache(_scan_cache)
        top_sym = top_ai[0]["symbol"] if top_ai else "none"
        bias_ct = sum(1 for s in symbol_bias if any(c["symbol"] == s for c in top_ai))
        print(f"[scan] Done. {ai_scored}/{len(top_ai)} AI-scored | top: {top_sym} | sector_bias: {bias_ct} stocks")

    except Exception as e:
        print(f"[scan] error: {e}")
        _scan_cache["sp500"] = {"status": "error", "progress": "error", "error": str(e), "candidates": []}
    finally:
        watchdog.cancel()
        _scan_running = False

    # Cascade: auto-run agent — debounced to prevent rapid re-triggering
    if cascade_agent and _scan_cache.get("sp500", {}).get("status") == "done":
        now = _time.time()
        if now - _last_cascade_at > 120:   # at least 2 min between cascades
            _last_cascade_at = now
            print("[scan] Cascading to trade agent…")
            _run_agent_internal()
        else:
            print(f"[scan] cascade skipped (last cascade {now - _last_cascade_at:.0f}s ago)")


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


# ── Scout endpoints ────────────────────────────────────────────────────────────

_scout_running: bool = False

@app.get("/api/scout/preview")
def get_scout_preview():
    """Return today's Scout-discovered dynamic tickers (from cache)."""
    from pathlib import Path
    scout_file = Path(__file__).parent.parent / "data" / "dynamic_tickers.json"
    try:
        data = json.loads(scout_file.read_text())
        return {
            "status":       "ok",
            "date":         data.get("date"),
            "tickers":      data.get("tickers", []),
            "count":        len(data.get("tickers", [])),
            "sources":      data.get("sources", {}),
            "generated_at": data.get("generated_at"),
        }
    except FileNotFoundError:
        return {"status": "not_run", "tickers": [], "count": 0}
    except Exception as e:
        return {"status": "error", "error": str(e), "tickers": [], "count": 0}


@app.post("/api/scout/run")
def trigger_scout(background_tasks: BackgroundTasks):
    """Manually trigger Scout dynamic discovery (runs in background)."""
    global _scout_running
    if _scout_running:
        return {"status": "already_running"}

    def _run_scout():
        global _scout_running
        _scout_running = True
        try:
            from src.monitor.scout import run as scout_run
            tickers = scout_run()
            print(f"[scout] Manual trigger done: {len(tickers)} tickers")
        except Exception as e:
            print(f"[scout] Manual trigger error: {e}")
        finally:
            _scout_running = False

    background_tasks.add_task(_run_scout)
    return {"status": "started"}


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
            "status": "done" if last_run else "not_run",
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

    # ── Inject trail_active from trades.json ──────────────────────────────────
    # When trail_active=True (+10% gain reached), Alpaca manages exit entirely.
    # holdings_monitor will bypass Claude AI for these positions.
    try:
        from src.trader.trade_agent import _load_from_disk
        _all_trades = _load_from_disk()
        _trail_map: dict[str, bool] = {}
        for _t in _all_trades.values():
            if _t.get("side") == "buy" and _t.get("symbol"):
                _sym = _t["symbol"]
                # Keep the most recent buy record per symbol
                if _sym not in _trail_map or _t.get("created_at", "") > _trail_map.get(_sym + "_at", ""):
                    _trail_map[_sym] = bool(_t.get("trail_active", False))
                    _trail_map[_sym + "_at"] = _t.get("created_at", "")
        for p in positions:
            p["trail_active"] = _trail_map.get(p["symbol"], False)
    except Exception as _e:
        print(f"[holdings] trail_active inject error: {_e}")

    _holdings_cache["positions"] = positions
    _holdings_cache["analyzed"] = False
    try:
        enriched = analyze_sell_signals(positions)
        from datetime import datetime as _dt
        _holdings_cache["positions"] = enriched
        _holdings_cache["analyzed"] = True
        _holdings_cache["refreshed_at"] = _dt.utcnow().isoformat()
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
        # Fetch all currently open orders (no limit — these are active and must all be visible)
        open_orders = alpaca.list_orders(status="open")
        # Fetch recent closed/filled/cancelled for activity history (last 30)
        recent_orders = alpaca.list_orders(status="closed", limit=30)
        # Merge: open orders first, then recent history (dedup by id)
        seen: set = set()
        merged = []
        for o in list(open_orders) + list(recent_orders):
            if o.id not in seen:
                seen.add(o.id)
                merged.append(o)

        def _fmt(o):
            return {
                "id": o.id,
                "symbol": o.symbol,
                "side": o.side,
                "qty": float(o.qty) if o.qty else None,
                "filled_qty": float(o.filled_qty) if o.filled_qty else 0,
                "filled_avg_price": float(o.filled_avg_price) if o.filled_avg_price else None,
                "limit_price": float(o.limit_price) if o.limit_price else None,
                "stop_price": float(o.stop_price) if o.stop_price else None,
                "status": o.status,
                "created_at": str(o.created_at),
                "type": o.type,
            }
        return [_fmt(o) for o in merged]
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
            portfolio_value = float(acct.portfolio_value)
            # Use equity-based cash to avoid margin spending
            from src.trader.alpaca_trader import get_client as _get_client
            _positions = _get_client().list_positions()
            _invested = sum(float(p.market_value) for p in _positions)
            cash = max(0.0, float(acct.equity) - _invested)
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

    # Auto-scan if missing or stale — never block Rex; kick off scan in background instead
    scan_data  = _scan_cache.get("sp500", {})
    scanned_at = scan_data.get("scanned_at")
    scan_status = scan_data.get("status", "not_run")
    needs_scan = True
    age_hours  = 0
    if scanned_at:
        try:
            age_hours  = (datetime.utcnow() - datetime.fromisoformat(scanned_at)).total_seconds() / 3600
            needs_scan = age_hours > SCAN_MAX_AGE_HOURS
        except Exception:
            needs_scan = True

    # Only scan during market hours (8:00–16:30 ET) — post-market data is garbage
    from datetime import timezone as _tz, timedelta as _td
    _et_now = datetime.now(_tz(_td(hours=-4)))
    _in_scan_window = _et_now.weekday() < 5 and (8, 0) <= (_et_now.hour, _et_now.minute) <= (16, 30)

    if scan_status == "running" or _scan_running:
        print("[agent] Scan already in progress — running Rex with last cached results")
    elif needs_scan and _in_scan_window:
        print(f"[agent] Scan {'missing' if not scanned_at else f'stale ({age_hours:.1f}h)'} — launching background scan & continuing with cached data")
        threading.Thread(target=_run_sp500_scan, kwargs={"cascade_agent": False}, daemon=True, name="bg-scan").start()
    elif needs_scan:
        print(f"[agent] Scan stale ({age_hours:.1f}h) but outside market hours — using cached data, will rescan at market open")
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


@app.get("/api/stats/performance")
def get_performance_stats():
    """已平仓交易的历史胜率 / 盈亏比统计，从 trade_history.json 实时计算。"""
    hist_file = Path(__file__).parent.parent / "data" / "trade_history.json"
    try:
        hist = json.loads(hist_file.read_text()) if hist_file.exists() else []
        closed = [t for t in hist if t.get("pnl_pct") is not None]
        wins   = [t for t in closed if t["pnl_pct"] > 0]
        losses = [t for t in closed if t["pnl_pct"] <= 0]
        avg_win  = sum(t["pnl_pct"] for t in wins)   / len(wins)   if wins   else 0.0
        avg_loss = abs(sum(t["pnl_pct"] for t in losses) / len(losses)) if losses else 0.0
        pf = (len(wins) * avg_win) / (len(losses) * avg_loss) if losses and avg_loss > 0 else 0.0
        return {
            "total":          len(closed),
            "wins":           len(wins),
            "losses":         len(losses),
            "win_rate":       round(len(wins) / len(closed) * 100, 1) if closed else 0.0,
            "avg_win_pct":    round(avg_win,  2),
            "avg_loss_pct":   round(-avg_loss, 2),
            "profit_factor":  round(pf, 2),
        }
    except Exception as e:
        return {"total": 0, "wins": 0, "losses": 0, "win_rate": 0.0,
                "avg_win_pct": 0.0, "avg_loss_pct": 0.0, "profit_factor": 0.0}


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


_compare_cache: dict = {}
_compare_running: bool = False

@app.post("/api/backtest/compare")
def trigger_compare(
    background_tasks: BackgroundTasks,
    symbols: str = "",
    period: str = "1y",
    hold_days: int = 10,
    target_pct: float = 0.08,
):
    """Compare 3 strategy variants: current 3% stop, wider 5% stop, strict entry."""
    global _compare_running
    if _compare_running:
        return {"status": "already_running"}

    sym_list = [s.strip().upper() for s in symbols.split(",") if s.strip()] if symbols else []
    if not sym_list:
        scan_candidates = (_scan_cache.get("sp500") or {}).get("candidates", [])
        sym_list = [c["symbol"] for c in scan_candidates if c.get("signal") in ("BUY", "STRONG_BUY")][:15]
    if not sym_list:
        sym_list = load_watchlist()

    def _run():
        global _compare_running
        _compare_running = True
        _compare_cache["result"] = {"status": "running", "symbols": sym_list}
        try:
            from src.analysis.backtester import compare_strategies
            result = compare_strategies(sym_list, period=period,
                                        hold_days=hold_days, target_pct=target_pct)
            _compare_cache["result"] = result
        except Exception as e:
            _compare_cache["result"] = {"status": "error", "error": str(e)}
        finally:
            _compare_running = False

    background_tasks.add_task(_run)
    return {"status": "started", "symbols": sym_list}


@app.get("/api/backtest/compare")
def get_compare():
    return _compare_cache.get("result", {"status": "not_run"})


# ── Version backtest: v_prev vs v_current ─────────────────────────────────────

_version_compare_cache: dict = {}
_version_compare_running: bool = False
_VERSION_COMPARE_FILE = Path(__file__).parent.parent / "data" / "version_compare_cache.json"


def _load_version_compare_from_disk():
    """Load persisted backtest result on startup so cache survives restarts."""
    try:
        if _VERSION_COMPARE_FILE.exists():
            data = json.loads(_VERSION_COMPARE_FILE.read_text())
            if data.get("status") == "done":
                _version_compare_cache["result"] = data
    except Exception:
        pass

_load_version_compare_from_disk()


@app.get("/api/backtest/versions")
def get_version_compare():
    import json
    from pathlib import Path
    result = _version_compare_cache.get("result", {"status": "not_run"})
    try:
        versions = json.loads((Path(__file__).parent.parent / "data" / "versions.json").read_text())
        result["versions_meta"] = [
            {"version": v["version"], "label": v["label"],
             "description": v["description"], "created_at": v["created_at"],
             "changes": v.get("changes", [])}
            for v in versions[-2:]
        ]
    except Exception:
        pass
    return result


@app.post("/api/backtest/versions")
def trigger_version_compare(
    background_tasks: BackgroundTasks,
    period: str = "1y",
    hold_days: int = 10,
):
    """Compare v_prev vs v_current using definitions in data/versions.json."""
    global _version_compare_running
    if _version_compare_running:
        return {"status": "already_running"}

    # Set flag immediately (before background task starts) to prevent race condition
    _version_compare_running = True
    _version_compare_cache["result"] = {"status": "running"}

    sym_list = load_backtest_universe(n=150)

    def _run():
        global _version_compare_running
        try:
            from src.analysis.backtester import backtest_compare_versions
            result = backtest_compare_versions(sym_list, period=period, hold_days=hold_days)
            _version_compare_cache["result"] = result
            try:
                import math as _math

                class _NumpySafeEncoder(json.JSONEncoder):
                    """Handle numpy/pandas scalar types that json can't serialize natively."""
                    def default(self, obj):
                        try:
                            import numpy as _np
                            if isinstance(obj, (_np.integer,)):
                                return int(obj)
                            if isinstance(obj, (_np.floating,)):
                                v = float(obj)
                                return None if (_math.isnan(v) or _math.isinf(v)) else v
                            if isinstance(obj, _np.ndarray):
                                return obj.tolist()
                        except ImportError:
                            pass
                        return super().default(obj)

                _VERSION_COMPARE_FILE.parent.mkdir(parents=True, exist_ok=True)
                tmp = _VERSION_COMPARE_FILE.with_suffix(".tmp")
                tmp.write_text(json.dumps(result, cls=_NumpySafeEncoder))
                tmp.replace(_VERSION_COMPARE_FILE)
            except Exception as _e:
                print(f"[backtest] disk save error: {_e}")
        except Exception as e:
            _version_compare_cache["result"] = {"status": "error", "error": str(e)}
        finally:
            _version_compare_running = False

    background_tasks.add_task(_run)
    return {"status": "started", "symbols": sym_list}


# ── Scheduler: auto-trigger after market close ────────────────────────────────

def _start_scheduler():
    """Background threads: Rex every 30 min during market hours + Vera at close + event triggers."""
    import time
    from datetime import datetime, timezone, timedelta

    ET = timezone(timedelta(hours=-4))   # EDT (approx; close enough for trading hours)

    # ── Shared state ────────────────────────────────────────────────────────────
    review_triggered:  set[str] = set()   # dates where close-review ran
    vera_extra_dates:  set[str] = set()   # dates where intraday Vera already fired
    scout_run_dates:   set[str] = set()   # dates where Scout already ran
    rex_last_run:      dict = {"ts": time.time()} # last Rex run timestamp; init to now to avoid immediate fire on startup
    last_regime:       dict = {"value": None}

    REX_INTERVAL_SECS  = 30 * 60   # 30 minutes
    DAILY_LOSS_TRIGGER = -0.02     # -2% daily loss triggers Vera
    POSITION_DD_TRIGGER = -0.05    # -5% position drawdown triggers Vera

    def _market_open(now_et: datetime) -> bool:
        h, m = now_et.hour, now_et.minute
        return now_et.weekday() < 5 and (9, 30) <= (h, m) <= (16, 0)

    def _check_event_triggers(today_str: str):
        """Fire Vera mid-day if daily loss or position drawdown threshold crossed."""
        if today_str in vera_extra_dates:
            return
        try:
            from src.monitor.portfolio_history import get_history
            hist = get_history()
            days = hist.get("days", [])
            if days:
                today_return = days[-1].get("daily_return_pct", 0) / 100
                if today_return <= DAILY_LOSS_TRIGGER:
                    print(f"[scheduler] Event trigger: daily loss {today_return:.1%} ≤ {DAILY_LOSS_TRIGGER:.0%} — running Vera")
                    vera_extra_dates.add(today_str)
                    _run_strategy_review()
                    return
        except Exception:
            pass
        try:
            from src.trader.alpaca_trader import get_positions
            for pos in get_positions():
                pct = float(getattr(pos, "unrealized_plpc", 0) or 0)
                if pct <= POSITION_DD_TRIGGER:
                    sym = getattr(pos, "symbol", "?")
                    print(f"[scheduler] Event trigger: {sym} drawdown {pct:.1%} — running Vera")
                    vera_extra_dates.add(today_str)
                    _run_strategy_review()
                    return
        except Exception:
            pass

    def _check_regime_change():
        """Detect regime flip and log it (future: adjust Rex behaviour)."""
        try:
            from src.monitor.market_regime import get_market_regime
            r = get_market_regime()
            current = r.get("regime")
            if last_regime["value"] and last_regime["value"] != current:
                print(f"[scheduler] Regime changed: {last_regime['value']} → {current}")
            last_regime["value"] = current
        except Exception:
            pass

    scan_triggered_times: set[str] = set()   # "YYYY-MM-DD HH:MM" already fired

    def _trigger_scan_cascade(label: str):
        """Launch SP500 scan + cascade to Rex in background."""
        if _scan_running:
            print(f"[scheduler] {label} scan skipped — already running")
            return
        print(f"[scheduler] {label} — launching SP500 scan + cascade")
        threading.Thread(
            target=_run_sp500_scan, kwargs={"cascade_agent": True},
            daemon=True, name=f"scan-{label}"
        ).start()

    def _loop():
        while True:
            now_et  = datetime.now(ET)
            today_str = now_et.strftime("%Y-%m-%d")
            h, m    = now_et.hour, now_et.minute
            is_weekday = now_et.weekday() < 5

            # ── P0: Scout pre-market discovery at 8:45 AM ET ──────────────────
            pre_market_scout = is_weekday and (h, m) == (8, 45)
            if pre_market_scout and today_str not in scout_run_dates:
                scout_run_dates.add(today_str)
                print(f"[scheduler] 8:45 AM pre-market — running Scout dynamic discovery")
                try:
                    import threading as _threading
                    def _scout_bg():
                        global _scout_running
                        _scout_running = True
                        try:
                            from src.monitor.scout import run as scout_run
                            tickers = scout_run()
                            print(f"[scheduler] Scout done: {len(tickers)} dynamic tickers added")
                        except Exception as _e:
                            print(f"[scheduler] Scout error: {_e}")
                        finally:
                            _scout_running = False
                    _threading.Thread(target=_scout_bg, daemon=True, name="scout").start()
                except Exception as e:
                    print(f"[scheduler] Scout launch error: {e}")

            # ── P0b: Fixed-time SP500 scans: 9:31, 11:00, 12:30, 14:30 ET ─────────
            SCAN_TIMES = {(9, 31), (11, 0), (12, 30), (14, 30)}
            scan_key = f"{today_str} {h:02d}:{m:02d}"
            if is_weekday and (h, m) in SCAN_TIMES and scan_key not in scan_triggered_times:
                scan_triggered_times.add(scan_key)
                _trigger_scan_cascade(f"{h:02d}:{m:02d}")

            # ── P1: Rex every 30 min during market hours ───────────────────────
            if _market_open(now_et):
                elapsed = time.time() - rex_last_run["ts"]
                if elapsed >= REX_INTERVAL_SECS:
                    print(f"[scheduler] Market open — running Rex (last run {elapsed/60:.0f}m ago)")
                    rex_last_run["ts"] = time.time()
                    try:
                        _run_agent_internal()
                    except Exception as e:
                        print(f"[scheduler] Rex error: {e}")

                # ── P2: Event-driven Vera ──────────────────────────────────────
                _check_event_triggers(today_str)
                _check_regime_change()

            # ── Close-of-day Vera at 4:15 PM ──────────────────────────────────
            after_close = (h, m) >= (16, 15)
            def _close_review_done(today_str: str) -> bool:
                if today_str in review_triggered:
                    return True
                gen_at = _review_cache.get("latest", {}).get("generated_at", "")
                if not gen_at or gen_at[:10] != today_str:
                    return False
                try:
                    gen_et = datetime.fromisoformat(gen_at).astimezone(ET)
                    return gen_et.hour >= 16
                except Exception:
                    return False
            review_done = _close_review_done(today_str)
            if is_weekday and after_close and not review_done:
                print(f"[scheduler] Market closed — triggering end-of-day review for {today_str}")
                review_triggered.add(today_str)
                try:
                    _run_strategy_review()
                except Exception as e:
                    print(f"[scheduler] Review error: {e}")

            time.sleep(60)

    t = threading.Thread(target=_loop, daemon=True, name="trading-scheduler")
    t.start()
    print("[scheduler] Autonomous scheduler started: Scout 8:45 AM + SP500 scan 9:31/11:00/12:30 + Rex every 30 min (market hours) + Vera at 4:15 PM ET")


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

    # Auto-create a new strategy version whenever overrides change
    try:
        from src.analysis.strategy_versions import create_version as _create_ver
        _create_ver(
            stop_loss_pct=updated.get("stop_loss_pct", 3.0),
            max_position_pct=updated.get("max_position_pct", 0.10),
            entry_rsi_max=updated.get("entry_rsi_max"),
            entry_vma20_max=updated.get("entry_vma20_max"),
            notes=req.reason or "",
        )
    except Exception as _ve:
        print(f"[overrides] strategy version creation failed (non-fatal): {_ve}")

    return updated


@app.get("/api/strategy/overrides/history")
def get_overrides_history():
    """Return version history of all override changes."""
    return _load_overrides_history()


# ── Strategy Log ──────────────────────────────────────────────────────────────

@app.get("/api/strategy/log")
def get_strategy_log(days: int = 30):
    """Return daily strategy snapshots for the last N days."""
    try:
        from src.analysis.strategy_logger import get_log
        return {"entries": get_log(days=days), "days": days}
    except Exception as e:
        return {"entries": [], "error": str(e)}


@app.post("/api/strategy/log/record")
def trigger_strategy_log_record():
    """Manually trigger a strategy log snapshot (useful for testing)."""
    try:
        from src.analysis.strategy_logger import record_daily_snapshot
        from src.trader.alpaca_trader import get_client as _get_alpaca
        candidates = (_scan_cache.get("sp500") or {}).get("candidates", [])
        entry = record_daily_snapshot(
            candidates=candidates,
            alpaca_api=_get_alpaca(),
        )
        return {"status": "recorded", "date": entry.get("date"),
                "quality_score": entry.get("scan_quality", {}).get("quality_score")}
    except Exception as e:
        return {"status": "error", "error": str(e)}


# ── Strategy Validation (Version Control + Stats) ────────────────────────────

@app.get("/api/strategy/versions")
def get_strategy_versions():
    from src.analysis.strategy_versions import get_all_versions, get_trade_history, compute_version_stats
    versions = get_all_versions()
    history  = get_trade_history()
    result = []
    for v in versions:
        trades = [t for t in history if t.get("strategy_version") == v["version"]]
        result.append({**v, "stats": compute_version_stats(trades)})
    return {"versions": result, "total_versions": len(versions)}


@app.get("/api/strategy/versions/compare")
def compare_strategy_versions(v1: str = "v1.0", v2: str = "v2.0"):
    from src.analysis.strategy_versions import compare_versions
    return compare_versions(v1, v2)


@app.get("/api/strategy/validation")
def get_validation_report():
    """Full validation report: current vs previous version + scan quality trend."""
    from src.analysis.strategy_versions import (
        get_all_versions, get_trade_history, compute_version_stats, compare_versions
    )
    from src.analysis.strategy_logger import get_log

    versions = get_all_versions()
    history  = get_trade_history()
    log      = get_log(days=30)

    current  = versions[-1] if versions else None
    previous = versions[-2] if len(versions) >= 2 else None

    comparison = None
    if current and previous:
        comparison = compare_versions(previous["version"], current["version"])

    # Scan quality trend (last 14 days)
    quality_trend = [
        {
            "date":          e["date"],
            "quality_score": e.get("scan_quality", {}).get("quality_score"),
            "rsi_mean":      e.get("scan_quality", {}).get("rsi_mean"),
            "vma20_mean":    e.get("scan_quality", {}).get("vma20_mean"),
            "signal_counts": e.get("scan_quality", {}).get("signal_counts"),
        }
        for e in log[-14:]
    ]

    all_stats = []
    for v in versions:
        trades = [t for t in history if t.get("strategy_version") == v["version"]]
        all_stats.append({"version": v["version"], "notes": v["notes"],
                          "created_at": v["created_at"], "stats": compute_version_stats(trades)})

    return {
        "current_version": current,
        "comparison":      comparison,
        "all_versions":    all_stats,
        "quality_trend":   quality_trend,
        "trade_history_count": len(history),
    }


@app.post("/api/strategy/versions/sync")
def sync_trade_history():
    """Pull closed trades from Alpaca and tag with strategy versions."""
    try:
        from src.analysis.strategy_versions import sync_closed_trades_from_alpaca
        from src.trader.alpaca_trader import get_client as _get_alpaca
        added = sync_closed_trades_from_alpaca(_get_alpaca(), days=30)
        return {"status": "ok", "new_trades_added": added}
    except Exception as e:
        return {"status": "error", "error": str(e)}


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


# ── Post-Mortem ───────────────────────────────────────────────────────────────

@app.get("/api/postmortem")
async def get_postmortem(days: int = 7, top_n: int = 3):
    """Generate AI post-mortem for the past N days of trades."""
    from src.analysis.postmortem import run_postmortem
    result = run_postmortem(days=days, top_n=top_n)
    return result


# ── Strategy Backtest ─────────────────────────────────────────────────────────

_strategy_backtest_running = False
_BACKTEST_RESULT_FILE = Path(__file__).parent.parent / "data" / "strategy_backtest_result.json"


def _run_strategy_backtest_job(months: int) -> None:
    global _strategy_backtest_running
    try:
        import sys, os
        scripts_dir = str(Path(__file__).parent.parent / "scripts")
        if scripts_dir not in sys.path:
            sys.path.insert(0, scripts_dir)
        from strategy_comparison_backtest import run_backtest_for_api
        result = run_backtest_for_api(months=months)
        result["status"] = "done"
        _BACKTEST_RESULT_FILE.parent.mkdir(exist_ok=True)
        with open(_BACKTEST_RESULT_FILE, "w") as f:
            import json as _json
            _json.dump(result, f)
    except Exception as e:
        err = {"status": "error", "error": str(e), "generated_at": __import__("datetime").datetime.now().isoformat()}
        with open(_BACKTEST_RESULT_FILE, "w") as f:
            import json as _json
            _json.dump(err, f)
    finally:
        _strategy_backtest_running = False


@app.post("/api/strategy-backtest/run")
async def run_strategy_backtest(months: int = 3, background_tasks: BackgroundTasks = None):
    global _strategy_backtest_running
    if _strategy_backtest_running:
        return {"status": "already_running"}
    _strategy_backtest_running = True
    import threading
    threading.Thread(target=_run_strategy_backtest_job, args=(months,), daemon=True).start()
    return {"status": "started"}


@app.get("/api/strategy-backtest/status")
async def get_strategy_backtest_status():
    last_result = None
    if _BACKTEST_RESULT_FILE.exists():
        try:
            import json as _json
            with open(_BACKTEST_RESULT_FILE) as f:
                last_result = _json.load(f)
        except Exception:
            pass
    return {"running": _strategy_backtest_running, "last_result": last_result}


# ── Serve built React app ─────────────────────────────────────────────────────

frontend_dist = Path(__file__).parent.parent / "frontend" / "dist"
if frontend_dist.exists():
    app.mount("/", StaticFiles(directory=str(frontend_dist), html=True), name="static")
