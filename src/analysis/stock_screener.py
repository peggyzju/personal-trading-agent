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
        candle_desc = c.get("candle_desc") or "N/A"
        candle_q    = c.get("candle_quality")
        candle_tag  = (
            "🕯️+2强" if candle_q == 2 else
            "🕯️+1好" if candle_q == 1 else
            "🕯️-1弱" if candle_q == -1 else
            "🕯️-2差" if candle_q == -2 else
            "🕯️中性"
        )
        row_line = (
            f'{i+1}. {sym} ({c.get("company_name") or sym}) | '
            f'sector={_sector_tag(c.get("sector","") or "")} | '
            f'pe={c.get("pe_ratio","N/A")} | mkt_cap={_fmt_mktcap(c.get("market_cap"))} | '
            f'beta={c.get("beta","N/A")} | momentum={c["momentum_5d"]:+.1f}% | '
            f'vol_ratio={c["volume_ratio"]:.1f}x | rsi={c["rsi"]:.0f} | '
            f'52w_pos={_52w_pos(c)} | tech_score={c["tech_score"]:.1f} | price=${c["price"]} | '
            f'candle={candle_tag} [{candle_desc}]'
        )
        extras = []
        info = (news_map or {}).get(sym, {})
        headlines = info.get("headlines", [])
        if headlines:
            extras.append("   News: " + " | ".join(f'"{h}"' for h in headlines[:2]))
        earnings_warning = info.get("earnings_warning")
        if earnings_warning:
            extras.append(f"   ⚠️  Earnings: {earnings_warning}")
        wsb = info.get("wsb_hype")
        if wsb and wsb.get("hype_label", "none") != "none":
            label   = wsb["hype_label"]
            mention = wsb["mentions"]
            delta   = wsb["hype_delta"]
            extras.append(f"   WSB: {label} ({mention} mentions, {delta:+.0f}% vs yesterday)")
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

    return f"""You are a professional swing trader analyzing stocks that passed a pullback/recovery screen.
These candidates have RSI < 60 and price within 8% of MA20 — they are NOT momentum breakouts; they are stocks in controlled setups with room to run.

YOUR ROLE: Technical filters (RSI, MA20, candle pattern, volume) have already been applied by the screening system. Do NOT re-evaluate or re-score those signals. Your value-add is assessing what the technical screen cannot see:
  1. News catalysts — is there a fresh fundamental driver supporting the setup?
  2. Earnings risk — are earnings within 5 days? (gap risk overrides technicals)
  3. Sector rotation — is this sector seeing inflows or outflows today?
  4. Fundamental quality — does PE/beta/market-cap profile fit a swing hold of 2-10 days?
Candle patterns and RSI levels below are provided as context only — use them to understand the setup, not to re-score momentum.
{ctx_section}{bias_section}{notes_section}
{rows}

Return a JSON array of {len(candidates)} objects. Each object must have exactly these fields:
- "symbol": string
- "ai_score": integer 1-10 (score based on catalyst quality + fundamental fit + sector tailwind — NOT a re-score of RSI/candle)
- "signal": one of "STRONG_BUY", "BUY", "HOLD", "SELL"
- "reason": one sentence on what the company does + one sentence on the NEWS or FUNDAMENTAL driver (not the candle pattern)
- "entry_note": "at market" | "on pullback to $X" | "wait for consolidation" | "avoid for now"
- "stop_loss_pct": number (e.g. 5.0 means 5% below current price — use 4-6% range)
- "target_pct": number (upside %, use 0 for SELL)
- "timeframe": "intraday" | "swing_2_5d" | "positional_1_2w" | "n/a"

Signal guidelines (calibrated against 6-month backtest, 835 trades):
- STRONG_BUY: candle🕯️+2 (bullish_engulf or strong_bull) + confirmed external catalyst (news, sector tailwind, MACD cross). Without a catalyst, max BUY.
- BUY: candle🕯️+2 with neutral catalyst, OR candle🕯️-2 showing strong_bear (NOT bearish_engulf) — oversold bounce where high-volume selloff near MA20 often recovers. RSI must be 35-60.
- HOLD: candle🕯️+1 (hammer/pullback_bull — backtest shows NOT reliable alone) OR candle🕯️0 (neutral). Do NOT enter without a catalyst.
- SELL: candle🕯️-1 (mild bearish — 24% WR in backtest) OR bearish_engulf (Exp -1.4%). Avoid.
- ALWAYS downgrade to HOLD if candle is doji🕯️— backtest Exp -1.2% regardless of other signals
- ALWAYS downgrade one level if earnings within 5 days (gap risk)
- ALWAYS downgrade one level if sector is underperforming and no independent catalyst
- WSB hype=extreme → ALWAYS downgrade one level (retail frenzy = likely near top; late-entry risk)
- WSB hype=moderate → ai_score may be +1 if technical setup is already strong (retail tailwind, not yet peaked)
- WSB hype=high → neutral (monitor; neither boost nor downgrade)

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
    from src.analysis.position_sizer import compute_structured_stop

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

    target_pct = item.get("target_pct") or 0.0
    try:
        target_pct = float(target_pct)
    except (TypeError, ValueError):
        target_pct = 0.0

    # ── Structured stop: MA20 × 0.99 or entry − 1.5×ATR (whichever is higher) ──
    # Prefer market-structure stop over Claude's suggested fixed %; fall back if
    # ATR/MA20 data is unavailable.
    atr = tech.get("atr")
    vs_ma20_pct = tech.get("vs_ma20_pct")
    ma20 = price / (1 + vs_ma20_pct / 100) if (vs_ma20_pct is not None and price) else None

    if price and (atr or ma20):
        stop_price = compute_structured_stop(price, ma20, atr)
    elif price:
        # Fallback: use Claude's suggested pct, clamped 3–8%
        claude_pct = item.get("stop_loss_pct") or 5.0
        try:
            claude_pct = max(3.0, min(8.0, float(claude_pct)))
        except (TypeError, ValueError):
            claude_pct = 5.0
        stop_price = round(price * (1 - claude_pct / 100), 2)
    else:
        stop_price = None

    stop_pct = round((price - stop_price) / price * 100, 2) if (price and stop_price) else 5.0

    return {
        **tech,
        **item,
        "signal":       signal,
        "ai_score":     ai_score,
        "reason":       item.get("reason") or "",
        "entry_note":   item.get("entry_note") or "at market",
        "timeframe":    item.get("timeframe") or "n/a",
        "stop_loss":    stop_price,
        "target_price": round(price * (1 + target_pct / 100), 2) if price and target_pct else None,
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

    # Build prompt and split into cacheable system part + dynamic user part
    full_prompt = _build_prompt(batch, strategy_notes, news_map, market_context, sector_bias)
    # Split at the first candidate line (line starting with "1.")
    split_idx = full_prompt.find("\n1. ")
    if split_idx > 0:
        system_part = full_prompt[:split_idx].strip()
        user_part   = full_prompt[split_idx:].strip()
    else:
        system_part = None
        user_part   = full_prompt

    for attempt in range(2):
        try:
            # Use prompt caching on the system instructions (saves ~90% input token cost on retries/similar calls)
            if system_part:
                msg = client.messages.create(
                    model="claude-sonnet-4-6",
                    max_tokens=2048,
                    system=[{
                        "type": "text",
                        "text": system_part,
                        "cache_control": {"type": "ephemeral"},
                    }],
                    messages=[{"role": "user", "content": user_part}],
                )
            else:
                msg = client.messages.create(
                    model="claude-sonnet-4-6",
                    max_tokens=2048,
                    messages=[{"role": "user", "content": full_prompt}],
                )
            text = msg.content[0].text
            # Log cache usage if available
            usage = getattr(msg, "usage", None)
            if usage:
                cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
                cache_write = getattr(usage, "cache_creation_input_tokens", 0) or 0
                if cache_read or cache_write:
                    print(f"[screener] prompt cache — read={cache_read} write={cache_write} tokens")
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
