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
CONFIDENCE_THRESHOLD = 0.75
MAX_SHARES = 1
AUTO_TRADE = os.getenv("AUTO_TRADE", "false").lower() == "true"


def run_analysis_cycle():
    import json
    from pathlib import Path
    from src.monitor.price_monitor import get_quote, get_ohlcv
    from src.monitor.news_monitor import get_news
    from src.analysis.ai_analyst import analyze
    from src.alerts.notifier import console_alert
    from api.app import _analysis_cache, _analysis_timestamps

    wl_file = Path("watchlist.json")
    watchlist = json.loads(wl_file.read_text()) if wl_file.exists() else WATCHLIST_DEFAULT

    print(f"\n--- Analysis cycle: {watchlist} ---")
    for symbol in watchlist:
        try:
            quote = get_quote(symbol)
            ohlcv = get_ohlcv(symbol)
            news = get_news(symbol)
            result = analyze(symbol, ohlcv, quote, news=news)
            result.update({"symbol": symbol, "price": quote["price"], "change_pct": quote["change_pct"]})
            _analysis_cache[symbol] = result
            import time as _t; _analysis_timestamps[symbol] = _t.time()

            console_alert(symbol, result.get("signal", "HOLD"), quote["price"], result.get("reasoning", ""))

            if AUTO_TRADE:
                confidence = result.get("confidence", 0)
                signal = result.get("signal", "HOLD")
                if confidence >= CONFIDENCE_THRESHOLD and signal != "HOLD":
                    from src.trader.alpaca_trader import place_order, get_account
                    acct = get_account()
                    if signal == "BUY" and float(acct.buying_power) > quote["price"] * MAX_SHARES:
                        order = place_order(symbol, "buy", MAX_SHARES)
                        print(f"  [ORDER] BUY {MAX_SHARES} {symbol} — id={order.id}")
                    elif signal == "SELL":
                        order = place_order(symbol, "sell", MAX_SHARES)
                        print(f"  [ORDER] SELL {MAX_SHARES} {symbol} — id={order.id}")
        except Exception as e:
            print(f"  [ERROR] {symbol}: {e}")


def run_sp500_scan():
    """Triggered at market open — scan S&P 500 for buy candidates."""
    from api.app import _run_sp500_scan
    print("[scheduler] Running daily S&P 500 scan…")
    _run_sp500_scan()


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
    """After analysis cycle — run signal engine and queue pending trades."""
    import json
    from pathlib import Path
    from api.app import _scan_cache, _holdings_cache, _analysis_cache, _analysis_timestamps
    from src.trader.trade_agent import run_agent

    wl_file = Path("watchlist.json")
    watchlist = json.loads(wl_file.read_text()) if wl_file.exists() else WATCHLIST_DEFAULT

    portfolio_value = 100_000.0
    try:
        from src.trader.alpaca_trader import get_account
        portfolio_value = float(get_account().portfolio_value)
    except Exception:
        pass

    print("[scheduler] Running trade agent signal scan…")
    summary = run_agent(
        scan_cache=_scan_cache,
        holdings_cache=_holdings_cache,
        watchlist=watchlist,
        portfolio_value=portfolio_value,
        analysis_cache=_analysis_cache,
        analysis_timestamps=_analysis_timestamps,
    )
    print(f"[scheduler] Agent done — {summary.get('trades_queued', 0)} trades queued")


if __name__ == "__main__":
    print("📎 Personal Trading Agent")
    print(f"   Auto-trade: {AUTO_TRADE} (paper trading)")
    print("   API: http://localhost:8000  |  Docs: http://localhost:8000/docs\n")

    scheduler = BackgroundScheduler()

    # S&P 500 scan: 9:31 AM ET Mon–Fri (convert to UTC: ET+4 = 13:31)
    scheduler.add_job(run_sp500_scan, "cron", day_of_week="mon-fri", hour=13, minute=31)

    # Analysis cycle + holdings: every 30 min during market hours Mon–Fri
    scheduler.add_job(run_analysis_cycle, "cron", day_of_week="mon-fri", hour="9-15", minute="*/30")
    scheduler.add_job(run_holdings_refresh, "cron", day_of_week="mon-fri", hour="9-15", minute="*/30")

    # Trade agent: pre-market 8:30 AM ET (UTC 12:30) — 1 hour before open
    scheduler.add_job(run_trade_agent, "cron", day_of_week="mon-fri", hour=12, minute=30,
                      id="agent_premarket", name="Pre-market agent scan (8:30 AM ET)")
    # Trade agent: runs after analysis cycle (offset by 2 min) + after scan
    scheduler.add_job(run_trade_agent, "cron", day_of_week="mon-fri", hour="9-15", minute="2,32")
    scheduler.add_job(run_trade_agent, "cron", day_of_week="mon-fri", hour=13, minute=40)  # after daily scan

    # Order fill sync: every 5 min during market hours
    scheduler.add_job(sync_order_fills, "cron", day_of_week="mon-fri", hour="9-16", minute="*/5")

    # Daily strategy review + email: 4:15 PM ET Mon–Fri (UTC 20:15)
    scheduler.add_job(run_daily_review, "cron", day_of_week="mon-fri", hour=20, minute=15,
                      id="daily_review", name="Daily strategy review (4:15 PM ET)")

    scheduler.start()
    print("[scheduler] Started (pre-market agent 8:30 AM ET | S&P scan 9:31 AM ET | analysis every 30 min | review 4:15 PM ET)\n")

    from api.app import app
    uvicorn.run(app, host="0.0.0.0", port=8000)
