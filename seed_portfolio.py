"""
Seed Alpaca paper account with your real Robinhood positions.

HOW TO USE
──────────
1. Open Robinhood app → Portfolio → note each position's symbol and share count.
2. Edit the ROBINHOOD_POSITIONS list below.
3. Run:  python3 seed_portfolio.py --dry-run    (preview, no orders placed)
        python3 seed_portfolio.py               (places real paper orders)

WHAT THIS DOES
──────────────
- Places a market BUY order for each position in your Alpaca PAPER account.
- Orders fill at today's market price (cost basis won't match Robinhood).
- P&L tracking in this agent starts from today's entry price.
- Requires enough virtual cash ($100k default). If your Robinhood portfolio
  is larger, reduce quantities proportionally or reset your paper account
  at https://app.alpaca.markets → Paper Trading → Reset Account.

NOTES
─────
- Only run ONCE. Re-running will double your positions.
- Fractional shares: Alpaca paper supports fractional qty (e.g. 1.5 shares).
- Skips symbols already held in Alpaca to avoid doubling.
"""
from __future__ import annotations
import argparse
import sys
import time

# ── ✏️  EDIT THIS LIST with your actual Robinhood positions ──────────────────
ROBINHOOD_POSITIONS: list[dict] = [
    # {"symbol": "AAPL",  "qty": 10},
    # {"symbol": "GOOGL", "qty": 5},
    # {"symbol": "TSLA",  "qty": 3},
    # {"symbol": "NVDA",  "qty": 2},
    # Add your positions here ↑
]
# ─────────────────────────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(description="Seed Alpaca paper account from Robinhood")
    parser.add_argument("--dry-run", action="store_true", help="Preview only, no orders placed")
    args = parser.parse_args()

    if not ROBINHOOD_POSITIONS:
        print("❌  ROBINHOOD_POSITIONS is empty. Edit seed_portfolio.py and add your holdings.")
        sys.exit(1)

    from dotenv import load_dotenv
    load_dotenv()

    from src.monitor.price_monitor import get_quote
    from src.trader.alpaca_trader import get_client, get_account

    api = get_client()
    acct = get_account()
    cash = float(acct.cash)
    print(f"\nAlpaca paper account: equity=${float(acct.equity):,.0f}  cash=${cash:,.0f}")

    # Get existing positions to skip duplicates
    existing = {p.symbol for p in api.list_positions()}
    if existing:
        print(f"Already held: {', '.join(sorted(existing))}")

    print(f"\n{'DRY RUN — ' if args.dry_run else ''}Seeding {len(ROBINHOOD_POSITIONS)} positions:\n")

    total_cost = 0.0
    orders = []

    for pos in ROBINHOOD_POSITIONS:
        symbol = pos["symbol"].upper()
        qty = float(pos["qty"])

        if symbol in existing:
            print(f"  ⏭️   {symbol:<8} — already held, skipped")
            continue

        try:
            q = get_quote(symbol)
            price = q["price"]
            est_cost = round(price * qty, 2)
            total_cost += est_cost
            orders.append({"symbol": symbol, "qty": qty, "price": price, "est_cost": est_cost})
            print(f"  📋  {symbol:<8} {qty:>6} shares × ${price:>8.2f} = ${est_cost:>10,.2f}")
        except Exception as e:
            print(f"  ❌  {symbol:<8} — price fetch failed: {e}")

    print(f"\n  Estimated total cost: ${total_cost:,.2f}")
    print(f"  Available cash:       ${cash:,.2f}")

    if total_cost > cash:
        shortage = total_cost - cash
        print(f"\n  ⚠️   Insufficient cash by ${shortage:,.2f}.")
        print("  Options:")
        print("    1. Reduce qty proportionally (multiply all qtys by {:.2f})".format(cash / total_cost))
        print("    2. Reset paper account at https://app.alpaca.markets to get $100k fresh")
        if not args.dry_run:
            print("\n  Aborting — run with reduced positions or reset account first.")
            sys.exit(1)

    if args.dry_run:
        print("\n  [DRY RUN] No orders placed. Remove --dry-run to execute.")
        return

    print(f"\nPlacing {len(orders)} orders…\n")
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
            print(f"  ✅  {o['symbol']:<8} {o['qty']} shares — order id={order.id}")
            placed += 1
            time.sleep(0.3)   # avoid rate limiting
        except Exception as e:
            print(f"  ❌  {o['symbol']:<8} — order failed: {e}")

    print(f"\n✅  Done — {placed}/{len(orders)} orders placed in Alpaca paper account.")
    print("Wait 1–2 minutes for fills, then refresh the Holdings tab in the UI.\n")


if __name__ == "__main__":
    main()
