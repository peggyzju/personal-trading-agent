from __future__ import annotations
import json
import os
import re

import anthropic
import pandas as pd


def analyze(symbol: str, ohlcv: pd.DataFrame, quote: dict, news: list[dict] | None = None) -> dict:
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    recent = ohlcv.tail(30)[["Open", "High", "Low", "Close", "Volume"]].to_string()

    news_section = ""
    if news:
        headlines = "\n".join(f'- [{n.get("source", "")}] {n["title"]}' for n in news[:6])
        news_section = f"\nRecent news:\n{headlines}\n"

    prompt = f"""You are a quantitative analyst. Analyze this US stock and give a trading recommendation.

Symbol: {symbol}
Current price: ${quote['price']:.2f}
Change today: {quote['change_pct']:+.2f}%
{news_section}
Recent 30-day OHLCV data:
{recent}

Consider both technical price action AND any news catalysts above.

Respond in JSON with these exact fields:
- signal: "BUY" | "SELL" | "HOLD"
- confidence: 0.0–1.0
- target_price: float
- stop_loss: float
- reasoning: 2–3 sentences covering price action and news drivers
- key_risks: list of 2–4 strings
- technical_notes: one sentence on chart pattern/momentum
- catalyst: the single most important news or fundamental driver (or null)"""

    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1024,
        messages=[{"role": "user", "content": prompt}],
    )

    text = message.content[0].text
    match = re.search(r"\{.*\}", text, re.DOTALL)
    return json.loads(match.group()) if match else {"raw": text}
