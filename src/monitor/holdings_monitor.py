from __future__ import annotations
import json
import os
import re

import anthropic

# Demo paper portfolio when Alpaca keys are not configured
DEMO_POSITIONS = [
    {"symbol": "AAPL", "qty": 5,  "avg_entry_price": 180.0, "current_price": 0, "market_value": 0,
     "unrealized_pl": 0, "unrealized_plpc": 0, "side": "long"},
    {"symbol": "NVDA", "qty": 2,  "avg_entry_price": 200.0, "current_price": 0, "market_value": 0,
     "unrealized_pl": 0, "unrealized_plpc": 0, "side": "long"},
    {"symbol": "TSLA", "qty": 3,  "avg_entry_price": 420.0, "current_price": 0, "market_value": 0,
     "unrealized_pl": 0, "unrealized_plpc": 0, "side": "long"},
]


def get_paper_positions() -> list[dict]:
    """Try Alpaca paper API; fall back to demo positions with live prices."""
    from src.monitor.price_monitor import get_quote

    try:
        from src.trader.alpaca_trader import get_client
        api = get_client()
        raw = api.list_positions()
        positions = [
            {
                "symbol": p.symbol,
                "qty": float(p.qty),
                "avg_entry_price": float(p.avg_entry_price),
                "current_price": float(p.current_price),
                "market_value": float(p.market_value),
                "unrealized_pl": float(p.unrealized_pl),
                "unrealized_plpc": float(p.unrealized_plpc) * 100,
                "side": p.side,
            }
            for p in raw
        ]
        if positions:
            return positions
    except Exception:
        pass

    # demo mode: fill in live prices
    positions = []
    for demo in DEMO_POSITIONS:
        p = dict(demo)
        try:
            q = get_quote(p["symbol"])
            p["current_price"] = q["price"]
            p["market_value"] = round(p["qty"] * q["price"], 2)
            p["unrealized_pl"] = round(p["market_value"] - p["qty"] * p["avg_entry_price"], 2)
            p["unrealized_plpc"] = round(
                (p["current_price"] - p["avg_entry_price"]) / p["avg_entry_price"] * 100, 2
            )
        except Exception:
            pass
        positions.append(p)
    return positions


def analyze_sell_signals(positions: list[dict]) -> list[dict]:
    """
    For each position, produce a sell signal assessment using Claude.
    Returns positions enriched with sell_signal, confidence, reason.
    """
    if not positions:
        return []

    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    rows = "\n".join(
        f'{i+1}. {p["symbol"]} | entry=${p["avg_entry_price"]:.2f} | '
        f'now=${p["current_price"]:.2f} | P&L={p["unrealized_plpc"]:+.1f}% | '
        f'qty={p["qty"]}'
        for i, p in enumerate(positions)
    )

    prompt = f"""You are a portfolio risk manager. Assess each position for a sell/hold decision.

Positions:
{rows}

For each position return a JSON object:
{{
  "symbol": "...",
  "sell_signal": "SELL" | "REDUCE" | "HOLD" | "ADD",
  "urgency": "HIGH" | "MEDIUM" | "LOW",
  "reason": "one sentence: key reason for this recommendation",
  "suggested_action": "e.g. sell all, sell half, hold with stop at $X, add X shares"
}}

Return ONLY a JSON array."""

    msg = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=1024,
        messages=[{"role": "user", "content": prompt}],
    )

    text = msg.content[0].text
    match = re.search(r"\[.*\]", text, re.DOTALL)
    if not match:
        return positions

    signals = json.loads(match.group())
    sig_map = {s["symbol"]: s for s in signals}

    return [
        {**p, **sig_map.get(p["symbol"], {"sell_signal": "HOLD", "urgency": "LOW", "reason": ""})}
        for p in positions
    ]
