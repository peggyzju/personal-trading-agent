import os
import anthropic
import pandas as pd


def analyze(symbol: str, ohlcv: pd.DataFrame, quote: dict) -> dict:
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    recent = ohlcv.tail(30)[["Open", "High", "Low", "Close", "Volume"]].to_string()
    prompt = f"""You are a quantitative analyst. Analyze this US stock and give a concise recommendation.

Symbol: {symbol}
Current price: ${quote['price']:.2f}
Change today: {quote['change_pct']:+.2f}%

Recent 30-day OHLCV data:
{recent}

Respond in JSON with these fields:
- signal: "BUY" | "SELL" | "HOLD"
- confidence: 0.0–1.0
- target_price: float
- stop_loss: float
- reasoning: one-paragraph summary
- key_risks: list of strings"""

    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1024,
        messages=[{"role": "user", "content": prompt}],
    )

    import json, re
    text = message.content[0].text
    match = re.search(r"\{.*\}", text, re.DOTALL)
    return json.loads(match.group()) if match else {"raw": text}
