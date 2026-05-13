"""
Daily strategy review generator.
Runs after market close (4:15 PM ET). Uses Claude to analyze:
- Today's P&L and trades
- What signals fired vs outcomes
- Core strategy health
- Specific iteration opportunities toward the 15%/month target
"""
from __future__ import annotations
import json
import re
from datetime import date, datetime
from typing import Optional


def _safe_float(v, default=0.0) -> float:
    try:
        return float(v) if v is not None else default
    except (TypeError, ValueError):
        return default


def generate_strategy_review(
    portfolio_history: dict,      # from get_history()
    executed_orders: list[dict],  # today's Alpaca orders
    agent_log: list[dict],        # trade agent run log
    agent_trades: list[dict],     # all agent trades (any status)
    scan_result: dict,            # latest S&P scan
    monthly_target_pct: float = 15.0,
) -> dict:
    """Call Claude to generate a structured end-of-day strategy review."""
    from src.config import get_anthropic_key
    import anthropic

    today_str = date.today().isoformat()

    # ── Pull today's P&L from history ────────────────────────────────────────
    days = portfolio_history.get("days", [])
    today_day = next((d for d in reversed(days) if d["date"] == today_str), None)
    daily_pl     = _safe_float(today_day["daily_pl"] if today_day else 0)
    daily_ret    = _safe_float(today_day["daily_return_pct"] if today_day else 0)
    current_equity = _safe_float(portfolio_history.get("current_equity", 0))

    # Monthly return so far (first trading day of the month to today)
    month_start = today_str[:7] + "-01"
    month_days = [d for d in days if d["date"] >= month_start]
    if month_days and month_days[0]["equity"] > 0:
        monthly_ret = (current_equity - (month_days[0]["equity"] - month_days[0]["daily_pl"])) \
                      / (month_days[0]["equity"] - month_days[0]["daily_pl"]) * 100
    else:
        monthly_ret = _safe_float(portfolio_history.get("total_return_pct", 0))

    target_gap = monthly_target_pct - monthly_ret

    # ── Today's agent activity ────────────────────────────────────────────────
    today_log = next((l for l in agent_log if l["run_at"][:10] == today_str), None)
    today_trades = [t for t in agent_trades if t.get("created_at", "")[:10] == today_str]
    executed_today = [t for t in today_trades if t["status"] == "executed"]
    rejected_today = [t for t in today_trades if t["status"] == "rejected"]
    expired_today  = [t for t in today_trades if t["status"] == "expired"]

    # ── Top scan candidates ───────────────────────────────────────────────────
    top_candidates = (scan_result.get("candidates") or [])[:5]
    candidates_txt = "\n".join(
        f"  {c['symbol']}: {c['signal']} score={c['ai_score']}/10 — {c.get('reason', '')[:80]}"
        for c in top_candidates
    ) or "  (no scan data)"

    # ── Today's orders summary ────────────────────────────────────────────────
    orders_txt = "\n".join(
        f"  {o['side'].upper()} {o['symbol']} qty={o.get('filled_qty', o.get('qty', '?'))} "
        f"@ ${_safe_float(o.get('filled_avg_price', 0)):.2f} [{o['status']}]"
        for o in executed_orders[:10]
    ) or "  No orders today"

    prompt = f"""You are a professional trading coach helping a retail investor build an automated trading agent.
The investor's goal is 15% monthly return. Today is {today_str}.

=== TODAY'S PERFORMANCE ===
Daily P&L: ${daily_pl:+,.0f} ({daily_ret:+.2f}%)
Month-to-date return: {monthly_ret:+.2f}% (target: {monthly_target_pct}%, gap: {target_gap:+.1f}%)
Portfolio equity: ${current_equity:,.0f}

=== TODAY'S TRADES (executed by agent) ===
{orders_txt}

=== AGENT ACTIVITY ===
Signals found today: {today_log['signals_found'] if today_log else 'N/A'}
Trades queued: {today_log['trades_queued'] if today_log else 'N/A'}
Executed: {len(executed_today)} | Rejected: {len(rejected_today)} | Expired: {len(expired_today)}

=== TOP SCAN CANDIDATES TODAY ===
{candidates_txt}

Please generate a structured daily strategy review. Be specific, quantitative, and actionable.
Focus on what changes would actually move the needle toward the 15% monthly target.

Return valid JSON only (no markdown):
{{
  "market_context": "<2 sentences: what the market did today, key themes>",
  "core_strategy_assessment": "<3-4 sentences: how the current strategy performed, what signal types are working>",
  "what_worked": ["<specific observation 1>", "<specific observation 2>"],
  "what_didnt": ["<specific gap 1>", "<specific gap 2>"],
  "monthly_progress_note": "<1 sentence on pace toward {monthly_target_pct}% target>",
  "iteration_opportunities": [
    {{
      "title": "<short title>",
      "description": "<2-3 sentences: what to change and expected impact>",
      "priority": "HIGH|MEDIUM|LOW",
      "expected_impact": "<e.g. +1-2% monthly return>"
    }},
    {{
      "title": "<short title>",
      "description": "<2-3 sentences>",
      "priority": "HIGH|MEDIUM|LOW",
      "expected_impact": "<expected impact>"
    }},
    {{
      "title": "<short title>",
      "description": "<2-3 sentences>",
      "priority": "HIGH|MEDIUM|LOW",
      "expected_impact": "<expected impact>"
    }}
  ],
  "tomorrow_focus": "<2-3 sentences: specific setups or watchlist names to watch tomorrow>",
  "one_line_summary": "<tweet-length summary of today's session>"
}}"""

    client = anthropic.Anthropic(api_key=get_anthropic_key())
    msg = client.messages.create(
        model="claude-opus-4-5",
        max_tokens=1500,
        messages=[{"role": "user", "content": prompt}],
    )
    raw = msg.content[0].text.strip()

    # Strip markdown code fences if present
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)

    review = json.loads(raw)
    review.update({
        "date": today_str,
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "performance": {
            "daily_pl": round(daily_pl, 2),
            "daily_return_pct": round(daily_ret, 2),
            "monthly_return_pct": round(monthly_ret, 2),
            "target_monthly_pct": monthly_target_pct,
            "target_gap": round(target_gap, 2),
            "current_equity": round(current_equity, 2),
        },
    })
    return review


