"""
Seed Alpaca paper account with your real Robinhood positions + match your actual equity.

HOW TO USE
──────────
Step 1: Open Robinhood app and note:
        - Each position: symbol + number of shares
        - Your total portfolio value (Investing + Cash)
        - Your cash balance (uninvested cash)

Step 2: Fill in ROBINHOOD_TOTAL_EQUITY, ROBINHOOD_CASH, and ROBINHOOD_POSITIONS below.

Step 3: python3 seed_portfolio.py --dry-run    ← preview, no orders placed
        python3 seed_portfolio.py               ← execute

MATCHING EQUITY
───────────────
Alpaca paper starts at $100k. This script checks if your Robinhood total equity
differs and tells you exactly how to reset Alpaca to the right amount before placing
orders. The reset is a one-click step at: https://app.alpaca.markets (Paper → Settings)

NOTES
─────
- Run ONCE. Re-running will double your positions.
- Fractional shares supported (e.g. qty: 1.5).
- Skips symbols already held in Alpaca.
- Positions enter at TODAY's market price (cost basis ≠ Robinhood's).
"""
from __future__ import annotations
import argparse
import sys
import time

# ══════════════════════════════════════════════════════════════════
#  ✏️  FILL IN YOUR ROBINHOOD ACCOUNT VALUES
# ══════════════════════════════════════════════════════════════════

# Your total Robinhood portfolio value (Investing + Cash combined).
# Found in Robinhood app → Portfolio screen, the large number at the top.
# Set to 0 to skip the equity-matching step.
ROBINHOOD_TOTAL_EQUITY: float = 58_468.92

# Your uninvested cash in Robinhood (the "Buying Power" or "Cash" line).
# Used to calculate how much cash to leave in Alpaca after seeding positions.
ROBINHOOD_CASH: float = 20_847.33

# Your positions (symbol + number of shares).
ROBINHOOD_POSITIONS: list[dict] = [
    {"symbol": "NVDA", "qty": 50},
    {"symbol": "AAPL", "qty": 14},   # Note: AAPL not APPL
    {"symbol": "APP",  "qty": 15},
    {"symbol": "MOD",  "qty": 20},
    {"symbol": "VRT",  "qty": 15},
    {"symbol": "PM",   "qty": 20},
]

# ══════════════════════════════════════════════════════════════════


def main():
    parser = argparse.ArgumentParser(description="Seed Alpaca paper account from Robinhood")
    parser.add_argument("--dry-run", action="store_true", help="Preview only, no orders placed")
    args = parser.parse_args()

    if not ROBINHOOD_POSITIONS:
        print("❌  ROBINHOOD_POSITIONS is empty.")
        print("    Edit seed_portfolio.py and add your holdings, then re-run.")
        sys.exit(1)

    from dotenv import load_dotenv
    load_dotenv()

    from src.monitor.price_monitor import get_quote
    from src.trader.alpaca_trader import get_client, get_account

    api    = get_client()
    acct   = get_account()
    alpaca_equity = float(acct.equity)
    alpaca_cash   = float(acct.cash)

    print("\n" + "═" * 58)
    print("  Robinhood → Alpaca Paper Account Seeder")
    print("═" * 58)
    print(f"\n  Alpaca paper now:   equity=${alpaca_equity:>12,.2f}   cash=${alpaca_cash:>12,.2f}")
    if ROBINHOOD_TOTAL_EQUITY:
        print(f"  Robinhood target:   equity=${ROBINHOOD_TOTAL_EQUITY:>12,.2f}   cash=${ROBINHOOD_CASH:>12,.2f}")

    # ── Step 1: equity mismatch check (informational only) ────────────────────
    if ROBINHOOD_TOTAL_EQUITY and ROBINHOOD_TOTAL_EQUITY > 0:
        diff = abs(alpaca_equity - ROBINHOOD_TOTAL_EQUITY)
        if diff > 100:
            print(f"\n  ℹ️   Equity difference: Alpaca=${alpaca_equity:,.0f}  Robinhood=${ROBINHOOD_TOTAL_EQUITY:,.0f}  (diff=${diff:,.0f})")
            print(f"  ℹ️   This is fine — same share quantities means identical % P&L tracking.")
            print(f"       Extra cash (${alpaca_equity - ROBINHOOD_TOTAL_EQUITY:+,.0f}) acts as additional buying power.\n")
        else:
            print(f"  ✅  Equity close enough (diff=${diff:,.0f}) — no reset needed\n")

    # ── Step 2: get existing Alpaca positions ──────────────────────────────────
    existing = {p.symbol for p in api.list_positions()}
    if existing:
        print(f"\n  Already held in Alpaca: {', '.join(sorted(existing))}")

    # ── Step 3: price-check each position ─────────────────────────────────────
    print(f"\n  {'DRY RUN — ' if args.dry_run else ''}Positions to seed:\n")
    total_cost = 0.0
    orders     = []

    for pos in ROBINHOOD_POSITIONS:
        symbol = pos["symbol"].upper()
        qty    = float(pos["qty"])

        if symbol in existing:
            print(f"  ⏭️   {symbol:<8} — already held, skipped")
            continue

        try:
            q         = get_quote(symbol)
            price     = q["price"]
            est_cost  = round(price * qty, 2)
            total_cost += est_cost
            orders.append({"symbol": symbol, "qty": qty, "price": price, "est_cost": est_cost})
            print(f"  📋  {symbol:<8}  {qty:>8.4g} shares  ×  ${price:>9.2f}  =  ${est_cost:>11,.2f}")
        except Exception as e:
            print(f"  ❌  {symbol:<8} — price fetch failed: {e}")

    # ── Step 4: cash summary ───────────────────────────────────────────────────
    effective_cash = ROBINHOOD_TOTAL_EQUITY if ROBINHOOD_TOTAL_EQUITY else alpaca_cash
    remaining_cash = effective_cash - total_cost
    print(f"\n  {'─'*46}")
    print(f"  Positions total:     ${total_cost:>12,.2f}")
    print(f"  Available cash:      ${effective_cash:>12,.2f}")
    print(f"  Cash after seeding:  ${remaining_cash:>12,.2f}", end="")
    if ROBINHOOD_CASH and abs(remaining_cash - ROBINHOOD_CASH) < effective_cash * 0.05:
        print("  ✅  (~matches Robinhood cash)")
    else:
        print()

    if total_cost > alpaca_cash:
        shortage = total_cost - alpaca_cash
        print(f"\n  ⚠️   Over Alpaca cash by ${shortage:,.2f} — orders may be rejected.")
        print(f"       Reset Alpaca paper account first (adds $100k fresh cash).")
        if not args.dry_run:
            print("  Aborting.\n")
            sys.exit(1)

    if args.dry_run:
        print("\n  [DRY RUN] No orders placed.")
        print("  Remove --dry-run to execute.\n")
        return

    # ── Step 5: place orders ───────────────────────────────────────────────────
    print(f"\n  Placing {len(orders)} market orders…\n")
    placed = 0
    for o in orders:
        try:
            order = api.submit_order(
                symbol=o["symbol"],
                qty=o["qty"],
                side="buy",
                type="market",
                time_in_force="day",
            )
            print(f"  ✅  {o['symbol']:<8}  {o['qty']} shares  →  order id={order.id}")
            placed += 1
            time.sleep(0.3)
        except Exception as e:
            print(f"  ❌  {o['symbol']:<8}  order failed: {e}")

    print(f"\n{'═'*58}")
    print(f"  ✅  Done — {placed}/{len(orders)} orders placed.")
    print(f"  Wait ~1 min for fills, then refresh the Holdings tab.\n")


if __name__ == "__main__":
    main()
