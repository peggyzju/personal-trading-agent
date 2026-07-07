#!/usr/bin/env python3
"""Write an Alpaca-vs-local reconciliation report.

Read-only against Alpaca. The report is intended to separate broker truth from
local cache/log drift after account/order incidents.
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.trader.alpaca_trader import get_client


ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "account_reconciliation_latest.json"


def _read_json(path: Path, default):
    try:
        return json.loads(path.read_text())
    except Exception:
        return default


def _order_row(order) -> dict:
    return {
        "id": order.id,
        "symbol": order.symbol,
        "side": order.side,
        "type": order.type,
        "qty": float(order.qty) if order.qty else None,
        "filled_qty": float(order.filled_qty) if order.filled_qty else 0.0,
        "filled_avg_price": float(order.filled_avg_price) if order.filled_avg_price else None,
        "stop_price": float(order.stop_price) if order.stop_price else None,
        "status": order.status,
        "created_at": str(order.created_at),
    }


def main() -> None:
    load_dotenv(".env")
    api = get_client()
    account = api.get_account()
    positions = api.list_positions()
    open_orders = api.list_orders(status="open")
    closed_orders = api.list_orders(status="closed", limit=120)

    position_symbols = {p.symbol for p in positions}
    orphan_open_sell_stops = [
        _order_row(o) for o in open_orders
        if o.side == "sell" and o.symbol not in position_symbols
    ]
    matching_open_stops = [
        _order_row(o) for o in open_orders
        if o.side == "sell" and o.symbol in position_symbols
    ]

    closed_since_july = [
        _order_row(o) for o in closed_orders
        if str(o.created_at) >= "2026-07-01"
    ]

    by_symbol = defaultdict(lambda: {"buy_qty": 0.0, "sell_qty": 0.0, "buy_notional": 0.0, "sell_notional": 0.0})
    for row in closed_since_july:
        if row["status"] != "filled" or not row["filled_qty"] or not row["filled_avg_price"]:
            continue
        bucket = by_symbol[row["symbol"]]
        qty = row["filled_qty"]
        notional = qty * row["filled_avg_price"]
        if row["side"] == "buy":
            bucket["buy_qty"] += qty
            bucket["buy_notional"] += notional
        elif row["side"] == "sell":
            bucket["sell_qty"] += qty
            bucket["sell_notional"] += notional

    net_flow = {}
    for symbol, row in sorted(by_symbol.items()):
        net_qty = row["buy_qty"] - row["sell_qty"]
        net_flow[symbol] = {
            **{k: round(v, 4) for k, v in row.items()},
            "net_qty": round(net_qty, 4),
            "position_qty": next((float(p.qty) for p in positions if p.symbol == symbol), 0.0),
        }

    local_trades = _read_json(ROOT / "data" / "trades.json", {})
    local_history = _read_json(ROOT / "data" / "trade_history.json", [])
    local_strategy_log = _read_json(ROOT / "data" / "strategy_log.json", [])
    latest_strategy = local_strategy_log[-1] if local_strategy_log else {}

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "broker_truth": {
            "account": {
                "status": account.status,
                "equity": float(account.equity),
                "cash": float(account.cash),
                "portfolio_value": float(account.portfolio_value),
                "buying_power": float(account.buying_power),
            },
            "positions": [
                {
                    "symbol": p.symbol,
                    "side": p.side,
                    "qty": float(p.qty),
                    "avg_entry_price": float(p.avg_entry_price),
                    "current_price": float(p.current_price),
                    "market_value": float(p.market_value),
                    "unrealized_pl": float(p.unrealized_pl),
                    "unrealized_plpc_pct": float(p.unrealized_plpc) * 100,
                }
                for p in positions
            ],
            "matching_open_stops": matching_open_stops,
            "orphan_open_sell_stops": orphan_open_sell_stops,
            "closed_orders_since_2026_07_01": closed_since_july,
            "net_filled_flow_since_2026_07_01": net_flow,
        },
        "local_state": {
            "trades_count": len(local_trades),
            "trade_history_count": len(local_history),
            "trade_history_latest_exit_date": max((r.get("exit_date", "") for r in local_history), default=None),
            "strategy_log_latest": latest_strategy,
        },
        "diagnosis": {
            "broker_is_primary_truth": True,
            "local_strategy_log_is_stale": bool(latest_strategy)
            and latest_strategy.get("portfolio", {}).get("positions") != len(positions),
            "local_trade_history_missing_recent_alpaca_fills": bool(local_history)
            and max((r.get("exit_date", "") for r in local_history), default="") < "2026-07-06",
            "open_orphan_sell_stops_count": len(orphan_open_sell_stops),
        },
    }

    OUT.write_text(json.dumps(report, indent=2, ensure_ascii=False))
    print(f"wrote {OUT}")
    print(json.dumps(report["diagnosis"], indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
