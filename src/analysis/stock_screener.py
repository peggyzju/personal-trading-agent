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
        vs_ma50 = c.get("vs_ma50_pct") or 0.0
        mom_1m  = c.get("momentum_1m") or 0.0
        mom_3m  = c.get("momentum_3m") or 0.0
        row_line = (
            f'{i+1}. {sym} ({c.get("company_name") or sym}) | '
            f'sector={_sector_tag(c.get("sector","") or "")} | '
            f'pe={c.get("pe_ratio","N/A")} | mkt_cap={_fmt_mktcap(c.get("market_cap"))} | '
            f'beta={c.get("beta","N/A")} | mom_5d={c["momentum_5d"]:+.1f}% | '
            f'mom_1m={mom_1m:+.1f}% | mom_3m={mom_3m:+.1f}% | vs_ma50={vs_ma50:+.1f}% | '
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

    return f"""You are a RISK SCREENER for a momentum/trend trading system (strategy v8).
The stocks below ALREADY passed a mechanical filter: uptrend (price > MA50, MA50 rising),
RSI 50-80, positive 3-month momentum, not over-extended. They are RANKED BY MOMENTUM and
will be bought mechanically in that order.

YOUR ONLY JOB — "排雷" (landmine veto): flag stocks to SKIP for reasons the price/volume
data CANNOT see. You do NOT rank, score, or select — mechanical momentum already did that.
Default to NO veto. Only veto when you can name a concrete, specific risk.

Veto categories (set veto=true only if one CLEARLY applies):
1. earnings_risk — earnings within ~3-5 days (gap risk on a fresh entry)
2. bad_catalyst — momentum collides with negative news: SEC/lawsuit/probe, guidance cut,
   dilution/secondary offering, key-exec exit, FDA reject, accounting concern
3. exhaustion — parabolic blow-off or news-spike likely to fade (sell-the-news); the
   catalyst is already widely known / fully priced
4. retail_frenzy — WSB extreme hype or short-squeeze driven (unsustainable, snap-back risk)
5. one_off — momentum from a one-time event (M&A rumor, single contract), not a durable trend
6. sector_breakdown — stock strong but its whole sector is rolling over / facing a clear
   regulatory or macro headwind
{ctx_section}{bias_section}{notes_section}
{rows}

Return a JSON array of {len(candidates)} objects, each EXACTLY:
- "symbol": string
- "veto": boolean (true = skip this momentum stock despite its rank)
- "veto_category": one of the 6 names above, or "" if no veto
- "veto_reason": ONE short sentence naming the specific risk (or "" if no veto)
- "ai_score": integer 1-10 — advisory confidence the trend CONTINUES (reference only, NOT
  used for buying; momentum rank decides buys)
- "reason": one short sentence on what the company does + the key driver

Be CONSERVATIVE: when unsure, veto=false (a wrong veto kills a good momentum winner).
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

    target_pct = item.get("target_pct") or 0.0
    try:
        target_pct = float(target_pct)
    except (TypeError, ValueError):
        target_pct = 0.0

    # v8: 固定 -8% 初始止损(= 回测验证值)。让赢家跑靠"宽止损 + 追踪止盈(+6%/-8%) +
    # 跌破MA20",不用 v7 结构化止损 —— 它对动量股(远在MA20上、低ATR)常钳到 -3%,
    # 会被正常回调打掉,直接违背"让赢家跑"。固定 -8% 给趋势呼吸空间。
    stop_price = round(price * 0.92, 2) if price else None
    stop_pct = 8.0 if stop_price else 5.0

    veto = bool(item.get("veto", False))
    return {
        **tech,
        **item,
        "signal":       signal,
        "ai_score":     ai_score,
        "veto":         veto,
        "veto_category": str(item.get("veto_category") or "") if veto else "",
        "veto_reason":  str(item.get("veto_reason") or "") if veto else "",
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
                    max_tokens=4096,
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
                    max_tokens=4096,
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
            print(f"[screener] JSON parse failed (attempt {attempt+1}/2). len={len(text)} tail[-100]: {text[-100:]}")
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
                    "stop_loss":    round(c["price"] * 0.92, 2) if c.get("price") else None,
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
            "stop_loss":    round(c["price"] * 0.92, 2) if c.get("price") else None,
            "target_price": None,
        })
    return fallback
