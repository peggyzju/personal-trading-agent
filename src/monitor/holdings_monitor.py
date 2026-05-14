from __future__ import annotations
import json
import os
import re

import anthropic
from src.config import get_anthropic_key

# Demo paper portfolio when Alpaca keys are not configured
DEMO_POSITIONS = [
    {"symbol": "NVDA", "qty": 1,  "avg_entry_price": 220.0, "current_price": 0, "market_value": 0,
     "unrealized_pl": 0, "unrealized_plpc": 0, "side": "long"},
    {"symbol": "APP",  "qty": 1,  "avg_entry_price": 300.0, "current_price": 0, "market_value": 0,
     "unrealized_pl": 0, "unrealized_plpc": 0, "side": "long"},
    {"symbol": "MOD",  "qty": 1,  "avg_entry_price": 100.0, "current_price": 0, "market_value": 0,
     "unrealized_pl": 0, "unrealized_plpc": 0, "side": "long"},
    {"symbol": "VRT",  "qty": 1,  "avg_entry_price": 150.0, "current_price": 0, "market_value": 0,
     "unrealized_pl": 0, "unrealized_plpc": 0, "side": "long"},
    {"symbol": "GOOG", "qty": 1,  "avg_entry_price": 170.0, "current_price": 0, "market_value": 0,
     "unrealized_pl": 0, "unrealized_plpc": 0, "side": "long"},
    {"symbol": "AAPL", "qty": 1,  "avg_entry_price": 200.0, "current_price": 0, "market_value": 0,
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


def _enrich_with_technicals(positions: list[dict]) -> list[dict]:
    """
    Add RSI, 5-day momentum, and ATR to each position for better sell decisions.
    Silently skips symbols that fail to fetch data.
    """
    from src.monitor.price_monitor import get_ohlcv
    from src.analysis.technical_indicators import compute_all

    enriched = []
    for p in positions:
        tech = {}
        try:
            ohlcv = get_ohlcv(p["symbol"], period="30d")
            if ohlcv is not None and len(ohlcv) >= 10:
                indicators = compute_all(ohlcv)
                closes = ohlcv["Close"].dropna()
                mom5 = (
                    (float(closes.iloc[-1]) - float(closes.iloc[-6])) / float(closes.iloc[-6]) * 100
                    if len(closes) >= 6 else 0
                )
                tech = {
                    "rsi": indicators.get("rsi"),
                    "macd_bullish": indicators.get("macd_bullish_cross", False),
                    "macd_bearish": indicators.get("macd_bearish_cross", False),
                    "vs_ma20_pct": indicators.get("vs_ma20_pct"),
                    "mom5d_pct": round(mom5, 2),
                    "atr_pct": indicators.get("atr_pct"),
                    "stop_2atr": indicators.get("stop_2atr"),
                }
        except Exception:
            pass
        enriched.append({**p, "_tech": tech})
    return enriched


def analyze_sell_signals(positions: list[dict]) -> list[dict]:
    """
    For each position, produce a sell signal assessment using Claude.
    Enriches positions with live technical indicators before analysis.
    Returns positions with sell_signal, urgency, reason, suggested_action added.
    """
    if not positions:
        return []

    client = anthropic.Anthropic(api_key=get_anthropic_key())

    # Enrich with technicals first
    enriched = _enrich_with_technicals(positions)

    def _tech_line(tech: dict) -> str:
        parts = []
        if tech.get("rsi") is not None:
            zone = "oversold" if tech["rsi"] < 35 else "overbought" if tech["rsi"] > 70 else "neutral"
            parts.append(f"RSI={tech['rsi']:.0f}({zone})")
        if tech.get("mom5d_pct") is not None:
            parts.append(f"5d_mom={tech['mom5d_pct']:+.1f}%")
        if tech.get("vs_ma20_pct") is not None:
            parts.append(f"vs_MA20={tech['vs_ma20_pct']:+.1f}%")
        if tech.get("macd_bullish"):
            parts.append("MACD_bullish_cross")
        if tech.get("macd_bearish"):
            parts.append("MACD_bearish_cross")
        if tech.get("atr_pct") is not None:
            parts.append(f"ATR={tech['atr_pct']:.1f}%/day")
        if tech.get("stop_2atr") is not None:
            parts.append(f"2xATR_stop=${tech['stop_2atr']:.2f}")
        return " | ".join(parts) if parts else "no tech data"

    rows = "\n".join(
        f'{i+1}. {p["symbol"]} | entry=${p["avg_entry_price"]:.2f} | '
        f'now=${p["current_price"]:.2f} | P&L={p["unrealized_plpc"]:+.1f}% | '
        f'qty={p["qty"]} | {_tech_line(p.get("_tech", {}))}'
        for i, p in enumerate(enriched)
    )

    prompt = f"""You are a portfolio risk manager. Assess each position for a sell/hold decision.
Use BOTH the P&L data AND the technical indicators (RSI, momentum, MACD, MA position).

Key guidelines:
- RSI < 35 + positive momentum → consider HOLD even if P&L is negative (possible recovery)
- RSI > 70 + bearish MACD cross → consider REDUCE or SELL even if P&L is positive
- Position below 2×ATR stop level → SELL (stop hit)
- MACD bullish cross + above MA20 → lean HOLD or ADD if fundamentals support
- Pure P&L alone is NOT sufficient reason to sell; combine with technicals

Positions:
{rows}

For each position return a JSON object:
{{
  "symbol": "...",
  "sell_signal": "SELL" | "REDUCE" | "HOLD" | "ADD",
  "urgency": "HIGH" | "MEDIUM" | "LOW",
  "reason": "one sentence citing both P&L and the key technical signal",
  "suggested_action": "e.g. sell all, sell half, hold with stop at $X, add X shares"
}}

Return ONLY a JSON array."""

    msg = client.messages.create(
        model="claude-haiku-4-5",
        max_tokens=1024,
        temperature=0,
        messages=[{"role": "user", "content": prompt}],
    )

    HARD_STOP_PCT = -3.0   # hard stop regardless of Claude output

    def _hard_stop_defaults(p: dict) -> dict:
        """Apply hard stop-loss rule independent of Claude analysis."""
        plpc = p.get("unrealized_plpc", 0)
        if plpc <= HARD_STOP_PCT:
            return {"sell_signal": "SELL", "urgency": "HIGH",
                    "reason": f"Hard stop: position down {plpc:.1f}% (threshold {HARD_STOP_PCT}%)"}
        return {"sell_signal": "HOLD", "urgency": "LOW", "reason": ""}

    text = msg.content[0].text
    match = re.search(r"\[.*?\]", text, re.DOTALL)
    sig_map: dict = {}
    if match:
        try:
            signals = json.loads(match.group())
            sig_map = {s["symbol"]: s for s in signals if isinstance(s, dict)}
        except Exception:
            pass

    result = []
    for p in enriched:
        default = _hard_stop_defaults(p)
        ai_sig  = sig_map.get(p["symbol"], {})
        # Claude overrides hard stop only if it also says SELL/REDUCE
        merged  = {**default, **ai_sig} if ai_sig.get("sell_signal") in ("SELL", "REDUCE") else {**default, **{k: v for k, v in ai_sig.items() if k != "sell_signal" and k != "urgency"}}
        # Hard stop always wins when plpc <= threshold
        if p.get("unrealized_plpc", 0) <= HARD_STOP_PCT:
            merged["sell_signal"] = "SELL"
            merged["urgency"]     = "HIGH"
        result.append({k: v for k, v in {**p, **merged}.items() if k != "_tech"})
    return result
