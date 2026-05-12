from __future__ import annotations
import json
import re

import anthropic
from src.config import get_anthropic_key


def ai_score_candidates(candidates: list[dict]) -> list[dict]:
    """
    Send top technical candidates to Claude in one batch call.
    Returns top 10 with AI scores, signals, reasons, and entry notes.
    """
    if not candidates:
        return []

    client = anthropic.Anthropic(api_key=get_anthropic_key())

    rows = "\n".join(
        f'{i+1}. {c["symbol"]} | momentum={c["momentum_5d"]:+.1f}% | '
        f'vol_ratio={c["volume_ratio"]:.1f}x | rsi={c["rsi"]} | '
        f'breakout={c["near_breakout"]} | tech_score={c["tech_score"]:.1f} | price=${c["price"]}'
        for i, c in enumerate(candidates)
    )

    prompt = f"""You are a professional equity analyst. These S&P 500 stocks passed technical screening today.

{rows}

Select the best 10 for a US retail trader to consider buying today. For each pick return a JSON object:
{{
  "symbol": "...",
  "ai_score": 1-10,
  "signal": "STRONG_BUY" | "BUY" | "WATCH",
  "reason": "one sentence: why this stock is compelling TODAY",
  "entry_note": "at market" | "on pullback to $X" | "on breakout above $X",
  "stop_loss_pct": suggested stop-loss % below current price (number, e.g. 3.5),
  "target_pct": suggested upside target % above current price (number),
  "timeframe": "intraday" | "swing_2_5d" | "positional_1_2w"
}}

Return ONLY a JSON array of exactly 10 objects, best first."""

    msg = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=2048,
        messages=[{"role": "user", "content": prompt}],
    )

    text = msg.content[0].text
    match = re.search(r"\[.*\]", text, re.DOTALL)
    if not match:
        return candidates[:10]

    ai_results = json.loads(match.group())

    # merge AI scores back onto technical data
    tech_map = {c["symbol"]: c for c in candidates}
    merged = []
    for a in ai_results:
        sym = a.get("symbol", "")
        tech = tech_map.get(sym, {})
        price = tech.get("price", 0)
        stop_pct = a.get("stop_loss_pct", 3.0)
        target_pct = a.get("target_pct", 6.0)
        merged.append({
            **tech,
            **a,
            "stop_loss": round(price * (1 - stop_pct / 100), 2),
            "target_price": round(price * (1 + target_pct / 100), 2),
        })
    return merged
