"""
Personal Trading Agent
Run: python3 main.py
  API:  http://localhost:8000
  Docs: http://localhost:8000/docs
"""
from __future__ import annotations
import os
import uvicorn
from dotenv import load_dotenv
from apscheduler.schedulers.background import BackgroundScheduler

load_dotenv()

WATCHLIST_DEFAULT = ["AAPL", "NVDA", "MSFT", "TSLA"]


ANALYSIS_MAX_AGE_HOURS = 2    # re-analyze if cache older than this
ANALYSIS_MOVE_THRESHOLD = 1.5  # re-analyze if price moved more than this %

def run_analysis_cycle():
    import json
    import time as _t
    from pathlib import Path
    from src.monitor.price_monitor import get_quote, get_ohlcv
    from src.monitor.news_monitor import get_news
    from src.analysis.ai_analyst import analyze
    from src.alerts.notifier import console_alert
    from api.app import _analysis_cache, _analysis_timestamps

    wl_file = Path("watchlist.json")
    watchlist = json.loads(wl_file.read_text()) if wl_file.exists() else WATCHLIST_DEFAULT

    print(f"\n--- Analysis cycle: {watchlist} ---")
    now = _t.time()
    for symbol in watchlist:
        try:
            quote = get_quote(symbol)
            current_price = quote["price"]

            # Skip if cache is fresh AND price hasn't moved significantly
            last_ts = _analysis_timestamps.get(symbol, 0)
            last_price = (_analysis_cache.get(symbol) or {}).get("price", 0)
            age_hours = (now - last_ts) / 3600
            price_move = abs(current_price - last_price) / last_price * 100 if last_price else 999

            if age_hours < ANALYSIS_MAX_AGE_HOURS and price_move < ANALYSIS_MOVE_THRESHOLD:
                print(f"  {symbol}: skip (age={age_hours:.1f}h, move={price_move:.2f}%)")
                continue

            ohlcv = get_ohlcv(symbol)
            news = get_news(symbol)
            result = analyze(symbol, ohlcv, quote, news=news)
            result.update({"symbol": symbol, "price": current_price, "change_pct": quote["change_pct"]})
            _analysis_cache[symbol] = result
            _analysis_timestamps[symbol] = now

            console_alert(symbol, result.get("signal", "HOLD"), current_price, result.get("reasoning", ""))
        except Exception as e:
            print(f"  [ERROR] {symbol}: {e}")


def run_market_context():
    """Step 1 of pipeline — generate market context (regime + goal progress + sector bias).
    Runs at 8:00 AM ET, before scan and agent."""
    from src.analysis.market_context import generate_market_context
    print("[scheduler] Generating market context…")
    ctx = generate_market_context()
    gc  = ctx.get("goal_context", {})
    print(
        f"[scheduler] Context: regime={ctx['regime']} aggression={ctx['aggression']} "
        f"min_score={ctx['min_ai_score']} "
        f"day={gc.get('days_elapsed')}/{gc.get('days_elapsed',0)+gc.get('days_remaining',0)-1} "
        f"return={gc.get('current_return_pct',0):+.2f}% "
        f"need/day={gc.get('daily_return_needed',0):.2f}%"
    )


def run_sp500_scan():
    """Step 2 of pipeline — scan S&P 500, then auto-cascade to agent."""
    from api.app import _run_sp500_scan
    print("[scheduler] Running daily S&P 500 scan (cascade→agent)…")
    _run_sp500_scan(cascade_agent=True)


def run_holdings_refresh():
    """Refresh paper holdings and sell signals every 30 min during market hours."""
    from src.monitor.holdings_monitor import get_paper_positions, analyze_sell_signals
    from api.app import _holdings_cache
    print("[scheduler] Refreshing holdings & sell signals…")
    positions = get_paper_positions()
    _holdings_cache["positions"] = positions
    try:
        enriched = analyze_sell_signals(positions)
        _holdings_cache["positions"] = enriched
        _holdings_cache["analyzed"] = True
    except Exception as e:
        print(f"[scheduler] holdings error: {e}")


def sync_order_fills():
    """Poll Alpaca every 5 min during market hours for fill status updates."""
    from src.trader.trade_agent import sync_fills
    changed = sync_fills()
    if changed:
        print(f"[scheduler] Fill sync: {len(changed)} order(s) updated")


def run_daily_review():
    """Generate end-of-day strategy review. 4:15 PM ET = UTC 20:15."""
    from api.app import _run_strategy_review
    print("[scheduler] Generating daily strategy review…")
    _run_strategy_review()


def run_trade_agent():
    """Step 3 of pipeline — run signal engine using market context for dynamic params."""
    import json
    from pathlib import Path
    from api.app import _scan_cache, _holdings_cache, _analysis_cache, _analysis_timestamps
    from src.trader.trade_agent import run_agent
    from src.analysis.market_context import load_market_context

    wl_file = Path("watchlist.json")
    watchlist = json.loads(wl_file.read_text()) if wl_file.exists() else WATCHLIST_DEFAULT

    portfolio_value = 100_000.0
    try:
        from src.trader.alpaca_trader import get_account
        portfolio_value = float(get_account().portfolio_value)
    except Exception:
        pass

    # ── Read market context → derive agent params ──────────────────────────
    ctx            = load_market_context()
    min_ai_score   = ctx.get("min_ai_score", 7)       # 6 / 7 / 8 based on aggression
    size_scale     = ctx.get("size_scale", 1.0)        # 0.75 / 1.0 / 1.1
    aggression     = ctx.get("aggression", "normal")
    goal_ctx       = ctx.get("goal_context", {})

    print(
        f"[scheduler] Running trade agent | aggression={aggression} "
        f"min_score={min_ai_score} size_scale={size_scale} "
        f"day {goal_ctx.get('days_elapsed','?')}/{goal_ctx.get('days_elapsed',0)+goal_ctx.get('days_remaining',0)-1} "
        f"gap={goal_ctx.get('gap_pct',0):.1f}%"
    )

    summary = run_agent(
        scan_cache=_scan_cache,
        holdings_cache=_holdings_cache,
        watchlist=watchlist,
        portfolio_value=portfolio_value,
        analysis_cache=_analysis_cache,
        analysis_timestamps=_analysis_timestamps,
        min_ai_score_override=min_ai_score,
        size_scale_override=size_scale,
    )
    print(f"[scheduler] Agent done — {summary.get('trades_queued', 0)} trades queued")


if __name__ == "__main__":
    print("📎 Personal Trading Agent")
    print("   API: http://localhost:8000  |  Docs: http://localhost:8000/docs\n")

    # All cron times are US/Eastern — explicit timezone avoids DST/UTC confusion
    ET = "America/New_York"
    scheduler = BackgroundScheduler(timezone=ET)

    # ── Pipeline: 市场分析 → 选股 → 执行 ────────────────────────────────────
    # Step 1: Market context (8:00 AM ET) — regime + goal progress + sector bias
    scheduler.add_job(run_market_context, "cron", day_of_week="mon-fri", hour=8, minute=0)
    # Step 2: S&P 500 scan (9:31 AM ET open + 12:30 PM midday) — uses market context
    scheduler.add_job(run_sp500_scan, "cron", day_of_week="mon-fri", hour=9, minute=31)
    scheduler.add_job(run_sp500_scan, "cron", day_of_week="mon-fri", hour=12, minute=30)
    # Step 3: Trade agent — cascades automatically from scan (9:31→agent, 12:30→agent)
    #         Also runs every 30 min intraday to catch sell signals & new setups
    scheduler.add_job(run_trade_agent, "cron", day_of_week="mon-fri", hour="10-15", minute="2,32")

    # Analysis cycle + holdings: every 30 min during market hours Mon–Fri (9 AM–3 PM ET)
    scheduler.add_job(run_analysis_cycle, "cron", day_of_week="mon-fri", hour="9-15", minute="*/30")
    scheduler.add_job(run_holdings_refresh, "cron", day_of_week="mon-fri", hour="9-15", minute="*/30")

    # Order fill sync: every 5 min during market hours
    scheduler.add_job(sync_order_fills, "cron", day_of_week="mon-fri", hour="9-16", minute="*/5")

    # Daily strategy review: 4:15 PM ET Mon–Fri (after market close)
    scheduler.add_job(run_daily_review, "cron", day_of_week="mon-fri", hour=16, minute=15,
                      id="daily_review", name="Daily strategy review (4:15 PM ET)")

    scheduler.start()
    print("[scheduler] Started — timezone: US/Eastern")
    print("  8:30 AM  pre-market agent | 9:31 AM scan | every 30 min analysis | 12:30 PM scan | 4:15 PM review\n")

    from api.app import app
    uvicorn.run(app, host="0.0.0.0", port=8000)
