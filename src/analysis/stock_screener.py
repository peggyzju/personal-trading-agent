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

    def _fmt_mktcap(mc) -> str:
        if not mc:
            return "N/A"
        if mc >= 1e12:
            return f"${mc/1e12:.1f}T"
        if mc >= 1e9:
            return f"${mc/1e9:.1f}B"
        return f"${mc/1e6:.0f}M"

    rows = "\n".join(
        f'{i+1}. {c["symbol"]} ({c.get("company_name") or c["symbol"]}) | '
        f'sector={c.get("sector","?") or "?"} | industry={c.get("industry","?") or "?"} | '
        f'pe={c.get("pe_ratio","N/A")} | mkt_cap={_fmt_mktcap(c.get("market_cap"))} | '
        f'beta={c.get("beta","N/A")} | momentum={c["momentum_5d"]:+.1f}% | '
        f'vol_ratio={c["volume_ratio"]:.1f}x | rsi={c["rsi"]} | '
        f'breakout={c["near_breakout"]} | tech_score={c["tech_score"]:.1f} | price=${c["price"]}'
        for i, c in enumerate(candidates)
    )

    prompt = f"""You are a professional equity analyst. These stocks passed a technical momentum screen today. Rate each one objectively — some may be worth buying, others may be overextended or lacking conviction.

{rows}

For each of the {len(candidates)} stocks, return a JSON object with an honest assessment:
{{
  "symbol": "...",
  "ai_score": 1-10,
  "signal": "STRONG_BUY" | "BUY" | "HOLD" | "SELL",
  "reason": "REQUIRED: start with one sentence describing what the company does (e.g. 'NVDA is a GPU and AI infrastructure company.'), then add the key trading factor in one more sentence.",
  "entry_note": "at market" | "on pullback to $X" | "on breakout above $X" | "avoid for now",
  "stop_loss_pct": suggested stop-loss % below current price (number, e.g. 3.5),
  "target_pct": suggested upside target % above current price (number, 0 if SELL),
  "timeframe": "intraday" | "swing_2_5d" | "positional_1_2w" | "n/a"
}}

Guidelines:
- STRONG_BUY: strong momentum, volume confirmation, not overbought, clear catalyst
- BUY: decent setup but less conviction — one concern (e.g. high RSI, thin volume)
- HOLD: mixed signals — passed the screen but risk/reward is unclear right now
- SELL: technically extended, RSI >75, weak volume, or breakout looks exhausted

Return ONLY a JSON array of exactly {len(candidates)} objects, one per stock."""

    msg = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=4096,
        messages=[{"role": "user", "content": prompt}],
    )

    text = msg.content[0].text
    match = re.search(r"\[.*?\]", text, re.DOTALL)
    if not match:
        return candidates[:10]

    try:
        ai_results = json.loads(match.group())
    except Exception:
        return candidates[:10]

    # merge AI scores back onto technical data
    tech_map = {c["symbol"]: c for c in candidates}
    merged = []
    for a in ai_results:
        sym = a.get("symbol", "")
        tech = tech_map.get(sym, {})
        if not tech:
            continue
        price = tech.get("price", 0)
        stop_pct = a.get("stop_loss_pct", 3.0)
        target_pct = a.get("target_pct", 0.0)
        merged.append({
            **tech,
            **a,
            "stop_loss": round(price * (1 - stop_pct / 100), 2) if stop_pct else None,
            "target_price": round(price * (1 + target_pct / 100), 2) if target_pct else None,
        })

    # sort: STRONG_BUY > BUY > HOLD > SELL, then by ai_score desc
    signal_rank = {"STRONG_BUY": 0, "BUY": 1, "HOLD": 2, "SELL": 3}
    merged.sort(key=lambda x: (signal_rank.get(x.get("signal", "HOLD"), 2), -x.get("ai_score", 0)))
    return merged
