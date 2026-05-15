from __future__ import annotations
import json
import re

import anthropic
import pandas as pd

from src.analysis.technical_indicators import compute_all, indicator_summary
from src.config import get_anthropic_key


def analyze(symbol: str, ohlcv: pd.DataFrame, quote: dict, news: list[dict] | None = None, strategy_notes: list[str] | None = None) -> dict:
    client = anthropic.Anthropic(api_key=get_anthropic_key())


    # Compute full indicator suite
    indicators = compute_all(ohlcv) if len(ohlcv) >= 20 else {}
    ind_text = indicator_summary(indicators) if indicators else "Insufficient data for indicators."

    # ATR-based stop loss (2×ATR); fall back to 3% if ATR not available
    price = quote["price"]
    atr_stop = indicators.get("stop_2atr")
    default_stop = round(price * 0.97, 2)
    suggested_stop = atr_stop if atr_stop and atr_stop > 0 else default_stop

    recent = ohlcv.tail(30)[["Open", "High", "Low", "Close", "Volume"]].to_string()

    news_section = ""
    if news:
        headlines = "\n".join(f'- [{n.get("source", "")}] {n["title"]}' for n in news[:6])
        news_section = f"\nRecent news:\n{headlines}\n"

    notes_section = ""
    if strategy_notes:
        notes_text = "\n".join(f"- {n}" for n in strategy_notes)
        notes_section = f"\nActive strategy guidelines (from recent reviews — apply these as additional filters):\n{notes_text}\n"

    prompt = f"""You are a quantitative analyst. Analyze this US stock and give a trading recommendation.

Symbol: {symbol}
Current price: ${price:.2f}
Change today: {quote['change_pct']:+.2f}%

Technical indicators:
{ind_text}
{news_section}{notes_section}
Recent 30-day OHLCV data:
{recent}

The ATR-based suggested stop loss is ${suggested_stop:.2f}. Use this as your stop_loss unless you have a strong reason to override it (e.g. a key support level is closer).

Consider both technical indicators AND any news catalysts. Pay attention to MACD crossovers, RSI extremes, Bollinger Band position, and MA alignment.

Respond in JSON with these exact fields:
- signal: "BUY" | "SELL" | "HOLD"
- confidence: 0.0–1.0
- target_price: float
- stop_loss: float
- reasoning: 2–3 sentences covering price action, indicator signals, and news drivers
- key_risks: list of 2–4 strings
- technical_notes: one sentence naming the strongest technical signal (e.g. MACD crossover, RSI oversold)
- catalyst: the single most important news or fundamental driver (or null)"""

    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1024,
        messages=[{"role": "user", "content": prompt}],
    )

    text = message.content[0].text
    match = re.search(r"\{.*\}", text, re.DOTALL)
    result = json.loads(match.group()) if match else {"raw": text}

    # Attach computed indicators so callers can use them
    result["indicators"] = indicators
    return result
