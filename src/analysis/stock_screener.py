from __future__ import annotations
import json
import re

import anthropic
from src.config import get_anthropic_key

# Required fields every AI-scored candidate must have
_REQUIRED = {"symbol", "ai_score", "signal", "reason"}
_VALID_SIGNALS = {"STRONG_BUY", "BUY", "HOLD", "SELL"}


def _build_prompt(
    candidates: list[dict],
    strategy_notes: list[str] | None,
    news_map: dict[str, dict] | None = None,
    market_context: dict | None = None,
    sector_bias: dict[str, str] | None = None,
) -> str:
    def _fmt_mktcap(mc) -> str:
        if not mc:
            return "N/A"
        if mc >= 1e12:
            return f"${mc/1e12:.1f}T"
        if mc >= 1e9:
            return f"${mc/1e9:.1f}B"
        return f"${mc/1e6:.0f}M"

    def _52w_pos(c) -> str:
        lo  = c.get("week52_low")
        hi  = c.get("week52_high")
        px  = c.get("price")
        if lo and hi and hi > lo and px:
            pct = (px - lo) / (hi - lo) * 100
            return f"{pct:.0f}%"
        return "N/A"

    def _sector_tag(sector: str) -> str:
        if not sector_bias or not sector:
            return sector or "?"
        # Match sector name loosely
        sector_lower = sector.lower()
        for key, bias in sector_bias.items():
            if key in sector_lower or sector_lower in key:
                arrow = "↑" if bias == "positive" else ("↓" if bias == "negative" else "")
                return f"{sector}{(' ' + arrow) if arrow else ''}"
        return sector

    rows_parts = []
    for i, c in enumerate(candidates):
        sym      = c["symbol"]
        row_line = (
            f'{i+1}. {sym} ({c.get("company_name") or sym}) | '
            f'sector={_sector_tag(c.get("sector","") or "")} | '
            f'pe={c.get("pe_ratio","N/A")} | mkt_cap={_fmt_mktcap(c.get("market_cap"))} | '
            f'beta={c.get("beta","N/A")} | momentum={c["momentum_5d"]:+.1f}% | '
            f'vol_ratio={c["volume_ratio"]:.1f}x | rsi={c["rsi"]:.0f} | '
            f'breakout={c["near_breakout"]} | 52w_pos={_52w_pos(c)} | '
            f'tech_score={c["tech_score"]:.1f} | price=${c["price"]}'
        )
        extras = []
        info = (news_map or {}).get(sym, {})
        headlines = info.get("headlines", [])
        if headlines:
            extras.append("   News: " + " | ".join(f'"{h}"' for h in headlines[:2]))
        earnings_warning = info.get("earnings_warning")
        if earnings_warning:
            extras.append(f"   ⚠️  Earnings: {earnings_warning}")
        rows_parts.append(row_line + ("\n" + "\n".join(extras) if extras else ""))

    rows = "\n".join(rows_parts)

    # Market context header
    ctx_section = ""
    if market_context:
        regime     = market_context.get("regime", "NEUTRAL")
        aggression = market_context.get("aggression", "normal")
        ctx_section = f"\nMarket context: {regime} regime, {aggression} aggression.\n"

    # Sector bias summary
    bias_section = ""
    if sector_bias:
        pos = [s for s, b in sector_bias.items() if b == "positive"]
        neg = [s for s, b in sector_bias.items() if b == "negative"]
        parts = []
        if pos: parts.append(f"outperforming: {', '.join(pos)}")
        if neg: parts.append(f"underperforming: {', '.join(neg)}")
        if parts:
            bias_section = f"Sector rotation today — {'; '.join(parts)}.\n"

    notes_section = ""
    if strategy_notes:
        notes_text = "\n".join(f"- {n}" for n in strategy_notes)
        notes_section = f"\nActive strategy guidelines:\n{notes_text}\n"

    return f"""You are a professional equity analyst. Rate each stock that passed a technical momentum screen.
{ctx_section}{bias_section}{notes_section}
{rows}

Return a JSON array of {len(candidates)} objects. Each object must have exactly these fields:
- "symbol": string
- "ai_score": integer 1-10
- "signal": one of "STRONG_BUY", "BUY", "HOLD", "SELL"
- "reason": one sentence on what the company does + one sentence on the key trading factor (incorporate news if relevant)
- "entry_note": "at market" | "on pullback to $X" | "on breakout above $X" | "avoid for now"
- "stop_loss_pct": number (e.g. 3.5 means 3.5% below current price)
- "target_pct": number (upside %, use 0 for SELL)
- "timeframe": "intraday" | "swing_2_5d" | "positional_1_2w" | "n/a"

Signal guidelines:
- STRONG_BUY: strong momentum + volume + not overbought + clear catalyst
- BUY: good setup but one concern (high RSI, thin volume, etc.)
- HOLD: mixed signals, unclear risk/reward
- SELL: overextended, RSI>75, weak volume
- Downgrade by 1 level if earnings are within 5 days (gap risk)
- Downgrade by 1 level if sector is underperforming and stock has no independent catalyst

Output raw JSON array only. No markdown, no explanation."""


def _parse_response(text: str) -> list[dict] | None:
    """Extract and parse JSON array from Claude response. Returns None on failure."""
    # Strip markdown code fences
    clean = re.sub(r"```(?:json|JSON)?\s*", "", text).strip()
    clean = clean.replace("```", "").strip()

    # Strategy 1: greedy match on outermost [...]
    m = re.search(r"\[.*\]", clean, re.DOTALL)
    if m:
        try:
            return json.loads(m.group())
        except Exception:
            pass

    # Strategy 2: first [ to last ]
    try:
        start = clean.index("[")
        end   = clean.rindex("]") + 1
        return json.loads(clean[start:end])
    except Exception:
        pass

    return None


def _validate_and_fill(item: dict, tech: dict) -> dict:
    """Ensure all required fields exist; fill defaults rather than leaving None."""
    price = tech.get("price", 0) or 0

    # Coerce signal
    signal = str(item.get("signal", "")).upper().strip()
    if signal not in _VALID_SIGNALS:
        signal = "HOLD"

    # Coerce ai_score
    try:
        ai_score = max(1, min(10, int(item.get("ai_score", 5))))
    except (TypeError, ValueError):
        ai_score = 5

    stop_pct   = item.get("stop_loss_pct") or 3.0
    target_pct = item.get("target_pct") or 0.0
    try:
        stop_pct   = float(stop_pct)
        target_pct = float(target_pct)
    except (TypeError, ValueError):
        stop_pct, target_pct = 3.0, 0.0

    return {
        **tech,
        **item,
        "signal":       signal,
        "ai_score":     ai_score,
        "reason":       item.get("reason") or "",
        "entry_note":   item.get("entry_note") or "at market",
        "timeframe":    item.get("timeframe") or "n/a",
        "stop_loss":    round(price * (1 - stop_pct / 100), 2) if price else None,
        "target_price": round(price * (1 + target_pct / 100), 2) if price and target_pct else None,
        # drop raw pct fields
        "stop_loss_pct":  stop_pct,
        "target_pct":     target_pct,
    }


def ai_score_candidates(
    candidates: list[dict],
    strategy_notes: list[str] | None = None,
    news_map: dict[str, dict] | None = None,
    market_context: dict | None = None,
    sector_bias: dict[str, str] | None = None,
) -> list[dict]:
    """
    Send top technical candidates to Claude. Returns candidates with AI scores.
    - Sends at most 15 candidates to reduce latency
    - Retries once if parse fails or all signals are missing
    - Fills field defaults instead of returning None
    - news_map: {symbol: {"headlines": [...], "earnings_warning": str|None}}
    - market_context: from market_context.json (regime, aggression, etc.)
    - sector_bias: {sector: "positive"/"negative"/"neutral"}
    """
    if not candidates:
        return []

    # Cap at 15 to keep Claude response tight and fast
    batch = candidates[:15]
    client = anthropic.Anthropic(api_key=get_anthropic_key())
    tech_map = {c["symbol"]: c for c in candidates}

    for attempt in range(2):
        prompt = _build_prompt(batch, strategy_notes, news_map, market_context, sector_bias)
        try:
            msg = client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=4096,
                temperature=0,
                messages=[{"role": "user", "content": prompt}],
            )
            text = msg.content[0].text
        except Exception as e:
            print(f"[screener] Claude API error (attempt {attempt+1}/2): {e}")
            if attempt == 0:
                import time; time.sleep(5)
                continue
            break

        ai_results = _parse_response(text)

        if ai_results is None:
            print(f"[screener] JSON parse failed (attempt {attempt+1}/2). Response[:300]: {text[:300]}")
            if attempt == 0:
                continue
            break

        # Validate and merge
        merged = []
        for item in ai_results:
            if not isinstance(item, dict):
                continue
            sym  = item.get("symbol", "")
            tech = tech_map.get(sym)
            if not tech:
                continue
            merged.append(_validate_and_fill(item, tech))

        if not merged:
            print(f"[screener] 0 valid items after validation (attempt {attempt+1}/2)")
            if attempt == 0:
                continue
            break

        # Check quality: if > half items missing signal, retry
        missing_signal = sum(1 for m in merged if m.get("signal") == "HOLD"
                             and not m.get("reason"))
        if missing_signal > len(merged) / 2 and attempt == 0:
            print(f"[screener] {missing_signal}/{len(merged)} items appear empty — retrying")
            continue

        # Add remaining candidates not scored by Claude (with tech data only)
        scored_syms = {m["symbol"] for m in merged}
        for c in candidates:
            if c["symbol"] not in scored_syms:
                merged.append({
                    **c,
                    "signal":       "HOLD",
                    "ai_score":     None,
                    "reason":       "",
                    "entry_note":   "",
                    "timeframe":    "n/a",
                    "stop_loss":    round(c["price"] * 0.97, 2) if c.get("price") else None,
                    "target_price": None,
                })

        signal_rank = {"STRONG_BUY": 0, "BUY": 1, "HOLD": 2, "SELL": 3}
        merged.sort(key=lambda x: (
            signal_rank.get(x.get("signal", "HOLD"), 2),
            -(x.get("ai_score") or 0),
        ))

        scored = sum(1 for m in merged if m.get("ai_score") is not None)
        print(f"[screener] {scored}/{len(merged)} candidates scored by AI")
        return merged

    # Complete fallback: return technical data with HOLD defaults
    print("[screener] Using technical-only fallback (all Claude attempts failed)")
    fallback = []
    for c in candidates[:10]:
        fallback.append({
            **c,
            "signal":       "HOLD",
            "ai_score":     None,
            "reason":       "AI scoring unavailable",
            "entry_note":   "",
            "timeframe":    "n/a",
            "stop_loss":    round(c["price"] * 0.97, 2) if c.get("price") else None,
            "target_price": None,
        })
    return fallback
