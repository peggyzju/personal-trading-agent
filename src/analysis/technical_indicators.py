from __future__ import annotations
import numpy as np
import pandas as pd


def compute_rsi(closes: pd.Series, period: int = 14) -> float:
    delta = closes.diff().dropna()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    rs = gain / loss.replace(0, np.nan)
    rsi = 100 - 100 / (1 + rs)
    val = rsi.dropna()
    return round(float(val.iloc[-1]), 1) if len(val) > 0 else 50.0


def compute_macd(closes: pd.Series) -> dict:
    """MACD (12/26/9). Returns line, signal, histogram, and crossover flag."""
    ema12 = closes.ewm(span=12, adjust=False).mean()
    ema26 = closes.ewm(span=26, adjust=False).mean()
    macd_line = ema12 - ema26
    signal_line = macd_line.ewm(span=9, adjust=False).mean()
    histogram = macd_line - signal_line

    prev_hist = float(histogram.iloc[-2]) if len(histogram) >= 2 else 0
    curr_hist = float(histogram.iloc[-1])

    return {
        "macd": round(float(macd_line.iloc[-1]), 4),
        "macd_signal": round(float(signal_line.iloc[-1]), 4),
        "macd_hist": round(curr_hist, 4),
        # True = histogram just crossed from negative to positive (bullish)
        "macd_bullish_cross": prev_hist < 0 < curr_hist,
        "macd_bearish_cross": prev_hist > 0 > curr_hist,
    }


def compute_bollinger(closes: pd.Series, period: int = 20, num_std: float = 2.0) -> dict:
    """Bollinger Bands. pct_b = where price sits inside the band (0=lower, 1=upper)."""
    ma = closes.rolling(period).mean()
    std = closes.rolling(period).std()
    upper = ma + num_std * std
    lower = ma - num_std * std

    price = float(closes.iloc[-1])
    mid = float(ma.iloc[-1])
    up = float(upper.iloc[-1])
    lo = float(lower.iloc[-1])
    band_width = up - lo

    pct_b = (price - lo) / band_width if band_width > 0 else 0.5

    return {
        "bb_upper": round(up, 2),
        "bb_mid": round(mid, 2),
        "bb_lower": round(lo, 2),
        "bb_pct_b": round(pct_b, 3),   # <0.2 = near lower band (oversold); >0.8 = near upper
        "bb_squeeze": round(band_width / mid * 100, 2) if mid > 0 else 0,  # % width
    }


def compute_moving_averages(closes: pd.Series) -> dict:
    """20 / 50 / 200-day MAs and price position relative to them."""
    price = float(closes.iloc[-1])
    result: dict = {}
    for period in (20, 50, 200):
        if len(closes) >= period:
            ma = float(closes.rolling(period).mean().iloc[-1])
            result[f"ma{period}"] = round(ma, 2)
            result[f"vs_ma{period}_pct"] = round((price - ma) / ma * 100, 2)
        else:
            result[f"ma{period}"] = None
            result[f"vs_ma{period}_pct"] = None
    # Golden/death cross (MA50 vs MA200)
    if result["ma50"] and result["ma200"]:
        result["golden_cross"] = result["ma50"] > result["ma200"]
    else:
        result["golden_cross"] = None
    return result


def compute_atr(df: pd.DataFrame, period: int = 14) -> dict:
    """Average True Range — measures real volatility including gaps."""
    high = df["High"]
    low = df["Low"]
    close = df["Close"]

    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)

    atr = float(tr.rolling(period).mean().dropna().iloc[-1])
    price = float(close.iloc[-1])

    return {
        "atr": round(atr, 2),
        "atr_pct": round(atr / price * 100, 2),  # ATR as % of price
        # Suggested stop distances
        "stop_1atr": round(price - atr, 2),
        "stop_2atr": round(price - 2 * atr, 2),
    }


def compute_all(df: pd.DataFrame) -> dict:
    """
    Run all indicators on an OHLCV DataFrame.
    Requires columns: Open, High, Low, Close, Volume.
    Returns a flat dict; any indicator that needs more data than available returns None for its fields.
    """
    closes = df["Close"].dropna()
    if len(closes) < 20:
        return {}

    result: dict = {}

    result["rsi"] = compute_rsi(closes)
    result.update(compute_macd(closes))

    if len(closes) >= 20:
        result.update(compute_bollinger(closes))

    result.update(compute_moving_averages(closes))

    if len(df) >= 15:
        result.update(compute_atr(df))

    return result


def indicator_summary(indicators: dict) -> str:
    """
    Produce a compact plain-text summary suitable for inclusion in an LLM prompt.
    """
    lines = []

    rsi = indicators.get("rsi")
    if rsi is not None:
        zone = "oversold" if rsi < 35 else "overbought" if rsi > 65 else "neutral"
        lines.append(f"RSI(14): {rsi} ({zone})")

    macd = indicators.get("macd")
    hist = indicators.get("macd_hist")
    if macd is not None:
        cross = ""
        if indicators.get("macd_bullish_cross"):
            cross = " ← BULLISH CROSSOVER"
        elif indicators.get("macd_bearish_cross"):
            cross = " ← BEARISH CROSSOVER"
        lines.append(f"MACD: {macd:.4f} | signal: {indicators.get('macd_signal', 0):.4f} | hist: {hist:.4f}{cross}")

    pct_b = indicators.get("bb_pct_b")
    if pct_b is not None:
        bb_zone = "near lower band (oversold)" if pct_b < 0.2 else "near upper band (overbought)" if pct_b > 0.8 else "mid-band"
        lines.append(f"Bollinger %B: {pct_b:.2f} ({bb_zone}) | band width: {indicators.get('bb_squeeze', 0):.1f}%")

    for period in (20, 50, 200):
        ma = indicators.get(f"ma{period}")
        vs = indicators.get(f"vs_ma{period}_pct")
        if ma is not None:
            direction = "above" if vs > 0 else "below"
            lines.append(f"MA{period}: ${ma:.2f} — price is {abs(vs):.1f}% {direction}")

    gc = indicators.get("golden_cross")
    if gc is not None:
        lines.append(f"MA50 vs MA200: {'Golden Cross (bullish)' if gc else 'Death Cross (bearish)'}")

    atr = indicators.get("atr")
    atr_pct = indicators.get("atr_pct")
    if atr is not None:
        lines.append(f"ATR(14): ${atr:.2f} ({atr_pct:.1f}% of price) | suggested stops: 1×ATR=${indicators['stop_1atr']:.2f}, 2×ATR=${indicators['stop_2atr']:.2f}")

    return "\n".join(lines)
