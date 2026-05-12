from __future__ import annotations
import json
import re
import anthropic
from src.config import get_anthropic_key


def analyze_news_sentiment(symbol: str, news_items: list[dict], price_change_pct: float) -> dict:
    """
    For each news item, assess trading relevance and sentiment.
    Returns per-item tags + overall signal.
    """
    if not news_items:
        return {"overall": "NEUTRAL", "items": [], "key_insight": "No recent news."}

    client = anthropic.Anthropic(api_key=get_anthropic_key())

    headlines = "\n".join(
        f'{i+1}. [{item["source"] or "News"}] {item["title"]}'
        for i, item in enumerate(news_items)
    )

    prompt = f"""You are a financial news analyst. Analyze these recent news headlines for {symbol}.

Current price change today: {price_change_pct:+.2f}%

Headlines:
{headlines}

For each headline, respond with a JSON array where each object has:
- "index": 1-based number
- "relevance": "HIGH" | "MEDIUM" | "LOW"
- "sentiment": "BULLISH" | "BEARISH" | "NEUTRAL"
- "impact": "IMMEDIATE" | "SHORT_TERM" | "LONG_TERM"
- "reason": one short sentence explaining the trading impact

Then add a final summary object with key "summary":
- "overall_sentiment": "BULLISH" | "BEARISH" | "NEUTRAL"
- "key_insight": one actionable sentence for a trader
- "watch_for": the single most important thing to monitor

Return ONLY valid JSON: {{"items": [...], "summary": {{...}}}}"""

    msg = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=1024,
        messages=[{"role": "user", "content": prompt}],
    )

    text = msg.content[0].text
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        return {"overall": "NEUTRAL", "items": [], "key_insight": "Could not parse response."}

    parsed = json.loads(match.group())
    summary = parsed.get("summary", {})
    items_raw = parsed.get("items", [])

    # merge sentiment tags back onto original news items
    tagged = []
    for item in news_items:
        idx = news_items.index(item) + 1
        tag = next((t for t in items_raw if t.get("index") == idx), {})
        tagged.append({**item, **tag})

    return {
        "overall": summary.get("overall_sentiment", "NEUTRAL"),
        "key_insight": summary.get("key_insight", ""),
        "watch_for": summary.get("watch_for", ""),
        "items": tagged,
    }
