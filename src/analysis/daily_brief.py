from __future__ import annotations
import json
import re
from datetime import date
import anthropic
from src.config import get_anthropic_key


def generate_daily_brief(watchlist_data: list[dict]) -> dict:
    """
    watchlist_data: list of {symbol, price, change_pct, news: [...], analysis: {...}}
    Returns a structured daily brief.
    """
    client = anthropic.Anthropic(api_key=get_anthropic_key())

    # Build context summary for Claude
    stock_summaries = []
    for d in watchlist_data:
        symbol = d.get("symbol", "")
        change = d.get("change_pct", 0)
        price = d.get("price", 0)
        news = d.get("news", [])
        signal = (d.get("analysis") or {}).get("signal", "")
        top_headlines = "; ".join(n["title"] for n in news[:3]) if news else "No recent news"
        stock_summaries.append(
            f"- {symbol}: ${price:.2f} ({change:+.2f}%) | Signal: {signal or 'N/A'} | News: {top_headlines}"
        )

    context = "\n".join(stock_summaries)
    today = date.today().strftime("%A, %B %d, %Y")

    prompt = f"""You are a professional market analyst generating a daily brief for a retail trader. Today is {today}.

Watchlist overview:
{context}

Generate a comprehensive daily market brief in JSON with these exact keys:
{{
  "headline": "one punchy sentence summarizing today's market mood",
  "market_mood": "RISK_ON" | "RISK_OFF" | "MIXED",
  "top_movers": [
    {{
      "symbol": "...",
      "change_pct": number,
      "reason": "1-2 sentences: what's driving this move and why it matters for traders"
    }}
  ],
  "key_events": [
    {{
      "event": "short event name",
      "impact": "BULLISH" | "BEARISH" | "NEUTRAL",
      "detail": "1 sentence"
    }}
  ],
  "trading_opportunities": [
    {{
      "symbol": "...",
      "action": "BUY" | "SELL" | "WATCH",
      "rationale": "2-3 sentences combining price action + news catalyst"
    }}
  ],
  "risks_to_watch": ["risk 1", "risk 2", "risk 3"],
  "sentiment_summary": "2-3 sentence paragraph: overall market narrative, what institutional money seems to be doing, and what retail traders should focus on today"
}}

Base your analysis on the news and price data provided. Be specific and actionable."""

    msg = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=2048,
        messages=[{"role": "user", "content": prompt}],
    )

    text = msg.content[0].text
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        return {"error": "Could not generate brief", "raw": text}

    result = json.loads(match.group())
    result["generated_at"] = date.today().isoformat()
    return result
