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
    from api.app import _analysis_cache

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

    scheduler.start()
    print("[scheduler] Started (S&P scan 9:31 AM ET | analysis every 30 min)\n")

    from api.app import app
    uvicorn.run(app, host="0.0.0.0", port=8000)
