"""
Personal Trading Agent
Run: python main.py
  API server: http://localhost:8000
  API docs:   http://localhost:8000/docs
"""

import os
import threading
import uvicorn
from dotenv import load_dotenv
from apscheduler.schedulers.background import BackgroundScheduler

load_dotenv()

WATCHLIST_DEFAULT = ["AAPL", "NVDA", "MSFT", "TSLA"]
CONFIDENCE_THRESHOLD = 0.75
MAX_SHARES = 1
AUTO_TRADE = os.getenv("AUTO_TRADE", "false").lower() == "true"


def run_cycle():
    import json
    from pathlib import Path
    from src.monitor.price_monitor import get_quote, get_ohlcv
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
            result = analyze(symbol, ohlcv, quote)
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


def start_scheduler():
    scheduler = BackgroundScheduler()
    scheduler.add_job(run_cycle, "cron", day_of_week="mon-fri", hour="9-15", minute="*/30")
    scheduler.start()
    print("Scheduler started (Mon–Fri 09:00–15:30 ET, every 30 min).")
    return scheduler


if __name__ == "__main__":
    print("Personal Trading Agent")
    print(f"Auto-trade: {AUTO_TRADE} (paper trading mode)")
    print("API: http://localhost:8000  |  Docs: http://localhost:8000/docs")

    run_cycle()
    scheduler = start_scheduler()

    from api.app import app
    uvicorn.run(app, host="0.0.0.0", port=8000)
