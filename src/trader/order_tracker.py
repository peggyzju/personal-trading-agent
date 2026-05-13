"""
Order Status Tracker

After approve_trade() submits an Alpaca order, we record the order_id.
sync_order_status() polls Alpaca for the real fill status and updates
the trade record in trades.json.

Tracked states (Alpaca → our label):
  filled            → "filled"        (fully executed, update fill_price)
  partially_filled  → "partial"       (some shares filled, note the rest)
  cancelled/expired → "cancelled"     (order didn't execute)
  rejected          → "rejected_fill" (Alpaca rejected after submission)
  pending_new / new → "pending_fill"  (submitted, awaiting execution)
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass


# Alpaca statuses that mean "order is done"
TERMINAL_STATUSES = {"filled", "cancelled", "expired", "rejected", "done_for_day"}


def sync_order_status(pending: dict[str, dict], save_fn) -> list[str]:
    """
    Check Alpaca for the current status of every trade with an order_id
    that isn't yet in a terminal fill state.

    Args:
        pending  : the _pending dict from trade_agent
        save_fn  : callable to persist changes (e.g. _save_to_disk)

    Returns list of trade_ids that changed status.
    """
    # Find trades that need checking
    to_check = [
        t for t in pending.values()
        if t.get("executed_order_id")
        and t.get("fill_status") not in TERMINAL_STATUSES
        and t["status"] == "executed"
    ]
    if not to_check:
        return []

    try:
        from src.trader.alpaca_trader import get_client
        alpaca = get_client()
    except Exception as e:
        print(f"[order_tracker] Alpaca connection failed: {e}")
        return []

    changed = []
    for trade in to_check:
        order_id = trade["executed_order_id"]
        try:
            order = alpaca.get_order(order_id)
            status = order.status   # Alpaca's status string

            prev_fill_status = trade.get("fill_status", "pending_fill")
            fill_qty   = float(order.filled_qty) if order.filled_qty else 0
            fill_price = float(order.filled_avg_price) if order.filled_avg_price else None

            trade["fill_status"]    = status
            trade["fill_qty"]       = fill_qty
            trade["fill_price"]     = fill_price
            trade["fill_checked_at"] = datetime.now(timezone.utc).isoformat()

            if status == "filled":
                trade["fill_note"] = (
                    f"Filled {fill_qty} shares @ ${fill_price:.2f}"
                    if fill_price else f"Filled {fill_qty} shares"
                )
            elif status == "partially_filled":
                trade["fill_note"] = f"Partial: {fill_qty} shares filled @ ${fill_price:.2f}"
            elif status in ("cancelled", "expired", "done_for_day"):
                trade["fill_note"] = f"Order {status} — {fill_qty} shares filled"
            elif status == "rejected":
                trade["fill_note"] = f"Alpaca rejected order: {getattr(order, 'reject_reason', 'unknown')}"
                trade["status"] = "error"
                trade["error"]  = trade["fill_note"]

            if status != prev_fill_status:
                changed.append(trade["id"])
                print(f"[order_tracker] {trade['symbol']} order {order_id[:8]}: {prev_fill_status} → {status}")

        except Exception as e:
            print(f"[order_tracker] Error checking order {order_id[:8]}: {e}")

    if changed:
        save_fn(pending)

    return changed
