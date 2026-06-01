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


def run_scout():
    """Pre-market dynamic ticker discovery via Finviz (9:00 AM ET Mon–Fri).
    Scout caches results for the day; calling it again is a no-op if already ran.
    """
    print("[scheduler] Running pre-market Scout discovery…")
    from api.agent_runs import record_agent_run
    try:
        from src.monitor.scout import run as scout_run
        tickers = scout_run()
        record_agent_run("scout", trigger="auto", result="success")
        print(f"[scheduler] Scout done: {len(tickers)} dynamic tickers discovered")
    except Exception as e:
        record_agent_run("scout", trigger="auto", result="fail", error=str(e))
        print(f"[scheduler] Scout error: {e}")


def run_market_context():
    """Step 1 of pipeline — generate market context (regime + goal progress + sector bias).
    Runs at 8:00 AM ET, before scan and agent."""
    from src.analysis.market_context import generate_market_context
    from api.agent_runs import record_agent_run
    print("[scheduler] Generating market context…")
    try:
        ctx = generate_market_context()
        record_agent_run("maya", trigger="auto", result="success")
    except Exception as e:
        record_agent_run("maya", trigger="auto", result="fail", error=str(e))
        print(f"[scheduler] Market context error: {e}")
        return
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
    from api.agent_runs import record_agent_run
    print("[scheduler] Running daily S&P 500 scan (cascade→agent)…")
    try:
        _run_sp500_scan(cascade_agent=True)
        record_agent_run("scout", trigger="auto", result="success")
    except Exception as e:
        record_agent_run("scout", trigger="auto", result="fail", error=str(e))
        print(f"[scheduler] S&P 500 scan error: {e}")


def run_holdings_refresh():
    """Refresh paper holdings and sell signals every 30 min during market hours.
    After analysis completes, cascade to Rex so sell signals are acted on immediately.
    """
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
        return

    # ── Cascade to Rex (sell-signal execution) ────────────────────────────────
    if positions:
        print("[scheduler] Holdings refresh done → cascading to Rex (sell signals)…")
        from api.agent_runs import record_agent_run
        try:
            from api.app import _run_agent_internal
            _run_agent_internal()
            record_agent_run("rex", trigger="auto", result="success")
        except Exception as e:
            record_agent_run("rex", trigger="auto", result="fail", error=str(e))
            print(f"[scheduler] Rex cascade error: {e}")


def sync_order_fills():
    """Poll Alpaca every 5 min during market hours for fill status updates."""
    from src.trader.trade_agent import sync_fills
    changed = sync_fills()
    if changed:
        print(f"[scheduler] Fill sync: {len(changed)} order(s) updated")



if __name__ == "__main__":
    print("📎 Personal Trading Agent")
    print("   API: http://localhost:8000  |  Docs: http://localhost:8000/docs\n")

    # All cron times are US/Eastern — explicit timezone avoids DST/UTC confusion
    ET = "America/New_York"
    # misfire_grace_time=60: 重启后若错过触发时间 ≤60s 则补跑，超过则跳过（避免任务雪崩）
    MGT = 60

    scheduler = BackgroundScheduler(timezone=ET)

    # ── Pipeline: 市场分析 → 选股 → 执行 ─────────────────────────────────────
    #
    #  8:00 AM  Market context (regime + goal progress + sector bias)
    #  8:45 AM  Maya/Scout pre-market dynamic discovery
    #  9:31 AM  扫描第1次 → cascade → Rex (buy signals)
    # 11:00 AM  扫描第2次 → cascade → Rex (buy signals)
    # 12:30 PM  扫描第3次 → cascade → Rex (buy signals)
    # 14:30 PM  扫描第4次 → cascade → Rex (buy signals)
    #  every 30 min  Holdings refresh → cascade → Rex (sell signals only)
    #  every 5  min  Fill sync (order status)
    #  Vera 复盘：已移除自动定时（数据太少噪音大），改为手动 trigger
    #            手动入口：POST /api/strategy/review（前端「复盘」Tab 按钮）
    # ──────────────────────────────────────────────────────────────────────────

    # Step 1: Market context
    scheduler.add_job(run_market_context, "cron", day_of_week="mon-fri", hour=8, minute=0,
                      id="market_context", name="Market context (8:00 AM ET)",
                      misfire_grace_time=MGT)

    # Step 0 (pre-market): Maya/Scout dynamic ticker discovery
    scheduler.add_job(run_scout, "cron", day_of_week="mon-fri", hour=8, minute=45,
                      id="scout", name="Maya/Scout pre-market discovery (8:45 AM ET)",
                      misfire_grace_time=MGT)

    # Step 2: S&P 500 scan × 4 — cascade_agent=True → auto-triggers Rex on completion
    scheduler.add_job(run_sp500_scan, "cron", day_of_week="mon-fri", hour=9, minute=31,
                      id="scan_0931", name="扫描第1次 + Rex cascade (9:31 AM ET)",
                      misfire_grace_time=MGT)
    scheduler.add_job(run_sp500_scan, "cron", day_of_week="mon-fri", hour=11, minute=0,
                      id="scan_1100", name="扫描第2次 + Rex cascade (11:00 AM ET)",
                      misfire_grace_time=MGT)
    scheduler.add_job(run_sp500_scan, "cron", day_of_week="mon-fri", hour=12, minute=30,
                      id="scan_1230", name="扫描第3次 + Rex cascade (12:30 PM ET)",
                      misfire_grace_time=MGT)
    scheduler.add_job(run_sp500_scan, "cron", day_of_week="mon-fri", hour=14, minute=30,
                      id="scan_1430", name="扫描第4次 + Rex cascade (2:30 PM ET)",
                      misfire_grace_time=MGT)

    # Step 3: Holdings refresh every 30 min → cascades to Rex for sell execution
    scheduler.add_job(run_holdings_refresh, "cron", day_of_week="mon-fri", hour="9-15", minute="*/30",
                      id="holdings_refresh", name="Holdings refresh + Rex sell cascade (every 30 min)",
                      misfire_grace_time=MGT)

    # Watchlist analysis cycle: every 30 min (independent of Rex)
    scheduler.add_job(run_analysis_cycle, "cron", day_of_week="mon-fri", hour="9-15", minute="*/30",
                      id="analysis_cycle", name="Watchlist analysis cycle (every 30 min)",
                      misfire_grace_time=MGT)

    # Order fill sync: every 5 min during market hours
    scheduler.add_job(sync_order_fills, "cron", day_of_week="mon-fri", hour="9-16", minute="*/5",
                      id="fill_sync", name="Order fill sync (every 5 min)",
                      misfire_grace_time=MGT)

    # Vera 复盘已移除自动定时 — 改为手动 trigger（POST /api/strategy/review）

    scheduler.start()
    print("[scheduler] Started (single source of truth — APScheduler, US/Eastern)")
    print("  8:45 AM Maya/Scout | 9:31/11:00/12:30/14:30 扫描→Rex | every 30min holdings→Rex")
    print("  Rex买入: 仅扫描后触发 (4次/天)  |  Rex卖出: 持仓监控后触发 (每30分钟)")
    print("  Vera 复盘: 已移除自动定时，改手动 trigger (POST /api/strategy/review)\n")

    from api.app import app
    uvicorn.run(app, host="0.0.0.0", port=8000)
