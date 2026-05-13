"""
Pre-market smoke test — run this before 9:30 AM ET to verify everything is wired correctly.
Usage:  python3 smoke_test.py
"""
from __future__ import annotations
import os
import sys
from pathlib import Path

# Load .env
from dotenv import load_dotenv
load_dotenv()

PASS = "✅"
FAIL = "❌"
WARN = "⚠️ "
results: list[tuple[str, str, str]] = []   # (status, name, detail)


def check(name: str, fn):
    try:
        detail = fn()
        results.append((PASS, name, detail or ""))
    except Exception as e:
        results.append((FAIL, name, str(e)))


# ── 1. Alpaca connection ──────────────────────────────────────────────────────
def _alpaca():
    from src.trader.alpaca_trader import get_account, get_client
    acct = get_account()
    positions = get_client().list_positions()
    return (f"equity=${float(acct.equity):,.0f}  cash=${float(acct.cash):,.0f}  "
            f"positions={len(positions)}  status={acct.status}")

check("Alpaca paper account", _alpaca)


# ── 2. Claude API ─────────────────────────────────────────────────────────────
def _claude():
    import anthropic
    from src.config import get_anthropic_key
    client = anthropic.Anthropic(api_key=get_anthropic_key())
    msg = client.messages.create(
        model="claude-haiku-4-5",
        max_tokens=10,
        messages=[{"role": "user", "content": "Reply OK"}],
    )
    return f"model=claude-haiku-4-5  response={msg.content[0].text.strip()!r}"

check("Claude API (Anthropic)", _claude)


# ── 3. Market data (yfinance) ─────────────────────────────────────────────────
def _market_data():
    from src.monitor.price_monitor import get_quote
    q = get_quote("SPY")
    return f"SPY=${q['price']:.2f}  change={q['change_pct']:+.2f}%"

check("Market data (yfinance / SPY)", _market_data)


# ── 4. Market regime ──────────────────────────────────────────────────────────
def _regime():
    from src.monitor.market_regime import get_market_regime
    r = get_market_regime(force_refresh=True)
    return (f"regime={r['regime']}  spy_vs_ma20={r['spy_vs_ma20']:+.1f}%  "
            f"block_buys={r['block_buys']}  size_factor={r['size_factor']}")

check("Market regime (SPY MA)", _regime)


# ── 5. Circuit breaker ────────────────────────────────────────────────────────
def _breaker():
    from src.monitor.circuit_breaker import get_circuit_breaker_state
    s = get_circuit_breaker_state()
    status = "TRIGGERED" if s.get("triggered") else "OK"
    return f"status={status}  daily_loss={s.get('daily_loss_pct', 0):.2f}%"

check("Circuit breaker", _breaker)


# ── 6. S&P 500 OHLCV sample ───────────────────────────────────────────────────
def _ohlcv():
    from src.monitor.price_monitor import get_ohlcv
    df = get_ohlcv("AAPL")
    return f"AAPL OHLCV rows={len(df)}  last_close=${df['Close'].iloc[-1]:.2f}"

check("OHLCV data (AAPL)", _ohlcv)


# ── 7. News feed ──────────────────────────────────────────────────────────────
def _news():
    from src.monitor.news_monitor import get_news
    items = get_news("AAPL", limit=3)
    return f"AAPL news items={len(items)}"

check("News feed", _news)


# ── 8. Watchlist file ─────────────────────────────────────────────────────────
def _watchlist():
    import json
    wl_file = Path("watchlist.json")
    wl = json.loads(wl_file.read_text()) if wl_file.exists() else []
    return f"symbols={wl}"

check("watchlist.json", _watchlist)


# ── 9. Data directory writable ───────────────────────────────────────────────
def _data_dir():
    data = Path("data")
    data.mkdir(exist_ok=True)
    test_file = data / ".write_test"
    test_file.write_text("ok")
    test_file.unlink()
    return "data/ directory writable"

check("data/ directory", _data_dir)


# ── 10. Trade agent queue ─────────────────────────────────────────────────────
def _trade_agent():
    from src.trader.trade_agent import get_pending_trades, get_agent_log
    pending = [t for t in get_pending_trades() if t["status"] == "pending"]
    return f"pending_trades={len(pending)}  log_entries={len(get_agent_log())}"

check("Trade agent queue", _trade_agent)


# ── Print results ─────────────────────────────────────────────────────────────
print("\n" + "═" * 60)
print("  Personal Trading Agent — Pre-market Smoke Test")
print("═" * 60)

failures = 0
for status, name, detail in results:
    pad = " " * max(1, 35 - len(name))
    print(f"  {status}  {name}{pad}{detail}")
    if status == FAIL:
        failures += 1

print("═" * 60)
if failures == 0:
    print(f"  ✅  All {len(results)} checks passed — ready to trade!\n")
    sys.exit(0)
else:
    print(f"  ❌  {failures} check(s) failed — fix before market open!\n")
    sys.exit(1)
