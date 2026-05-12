"""
Personal Trading Agent
Run: python main.py
"""

import os
from dotenv import load_dotenv
from apscheduler.schedulers.blocking import BlockingScheduler

from src.monitor.price_monitor import get_quote, get_ohlcv
from src.analysis.ai_analyst import analyze
from src.alerts.notifier import console_alert

load_dotenv()

# Stocks to watch — edit this list
WATCHLIST = ["AAPL", "NVDA", "MSFT", "TSLA"]

# Min confidence to act on a signal
CONFIDENCE_THRESHOLD = 0.75

# Max shares per order (set low for safety)
MAX_SHARES = 1

AUTO_TRADE = os.getenv("AUTO_TRADE", "false").lower() == "true"


def run_cycle():
    print(f"\n--- Running analysis cycle ---")
    for symbol in WATCHLIST:
        try:
            quote = get_quote(symbol)
            ohlcv = get_ohlcv(symbol)
            result = analyze(symbol, ohlcv, quote)

            signal = result.get("signal", "HOLD")
            confidence = result.get("confidence", 0)
            reasoning = result.get("reasoning", "")

            console_alert(symbol, signal, quote["price"], reasoning)

            if AUTO_TRADE and confidence >= CONFIDENCE_THRESHOLD and signal != "HOLD":
                from src.trader.alpaca_trader import place_order, get_account
                acct = get_account()
                buying_power = float(acct.buying_power)
                if signal == "BUY" and buying_power > quote["price"] * MAX_SHARES:
                    order = place_order(symbol, "buy", MAX_SHARES)
                    print(f"  [ORDER] BUY {MAX_SHARES} {symbol} @ market — id={order.id}")
                elif signal == "SELL":
                    order = place_order(symbol, "sell", MAX_SHARES)
                    print(f"  [ORDER] SELL {MAX_SHARES} {symbol} @ market — id={order.id}")

        except Exception as e:
            print(f"  [ERROR] {symbol}: {e}")


if __name__ == "__main__":
    print("Personal Trading Agent starting...")
    print(f"Watchlist: {WATCHLIST}")
    print(f"Auto-trade: {AUTO_TRADE}")

    run_cycle()

    scheduler = BlockingScheduler()
    # Run every 30 minutes during market hours (Mon–Fri 9:30–16:00 ET)
    scheduler.add_job(run_cycle, "cron", day_of_week="mon-fri", hour="9-15", minute="*/30")
    print("\nScheduler started. Press Ctrl+C to stop.")
    scheduler.start()
