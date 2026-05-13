from __future__ import annotations
import pandas as pd
import yfinance as yf
import numpy as np
from src.analysis.technical_indicators import compute_all


def get_sp500_tickers() -> list[str]:
    try:
        table = pd.read_html("https://en.wikipedia.org/wiki/List_of_S%26P_500_companies")[0]
        return table["Symbol"].str.replace(".", "-", regex=False).tolist()
    except Exception:
        # fallback: a representative subset
        return [
            "AAPL","MSFT","NVDA","AMZN","GOOGL","META","TSLA","BRK-B","UNH","JPM",
            "V","XOM","LLY","JNJ","MA","AVGO","PG","HD","MRK","COST","ABBV","CVX",
            "KO","PEP","BAC","TMO","CSCO","MCD","ACN","ORCL","ABT","CRM","ADBE",
            "DHR","NKE","LIN","DIS","TXN","PM","NFLX","RTX","UPS","INTU","QCOM",
            "AMD","SPGI","AMGN","HON","CAT","SBUX","LOW","GS","BA","ELV","AXP",
        ]


def compute_technicals(df: pd.DataFrame) -> dict:
    """Compute full technical picture from OHLCV DataFrame."""
    closes = df["Close"].dropna()
    volumes = df["Volume"].dropna()
    if len(closes) < 6:
        return {}

    price_now = float(closes.iloc[-1])

    # 5-day momentum
    price_5d = float(closes.iloc[-6]) if len(closes) >= 6 else float(closes.iloc[0])
    momentum_5d = (price_now - price_5d) / price_5d * 100

    # Volume ratio: use previous COMPLETED day (iloc[-2]) to avoid partial intraday bar
    vol_prev = float(volumes.iloc[-2]) if len(volumes) >= 2 else float(volumes.iloc[-1])
    vol_avg = float(volumes.iloc[-22:-2].mean()) if len(volumes) >= 22 else float(volumes.iloc[:-1].mean())
    volume_ratio = vol_prev / vol_avg if vol_avg > 0 else 1.0

    # 20-day breakout (use completed bars only)
    high_20d = float(closes.iloc[-21:-1].max()) if len(closes) >= 21 else float(closes.max())
    near_breakout = price_now >= high_20d * 0.98

    # Full indicator suite
    indicators = compute_all(df)
    rsi = indicators.get("rsi", 50.0)

    # Composite tech score (0–100 scale)
    score = (
        min(max(momentum_5d, 0), 10) * 3.5          # up to 35 pts
        + min(max(volume_ratio - 1, 0), 3) * 10 * 0.25  # up to 7.5 pts
        + (15 if near_breakout else 0)               # 15 pts
        + (10 if indicators.get("macd_bullish_cross") else 0)  # 10 pts
        + (10 if indicators.get("bb_pct_b", 0.5) > 0.55 else 0)  # 10 pts: price above BB midline (bullish momentum)
        + (10 if (indicators.get("vs_ma20_pct") or 0) > 0
                 and (indicators.get("vs_ma50_pct") or 0) > 0 else 0)  # aligned above MAs
        + max(0, (70 - rsi)) * 0.5                   # reward stocks not yet overbought
    )

    result = {
        "price": round(price_now, 2),
        "momentum_5d": round(momentum_5d, 2),
        "volume_ratio": round(volume_ratio, 2),
        "near_breakout": near_breakout,
        "rsi": rsi,
        "tech_score": round(score, 2),
    }
    # Forward extra indicators useful for AI scoring
    for key in ("macd_bullish_cross", "macd_bearish_cross", "bb_pct_b",
                "vs_ma20_pct", "vs_ma50_pct", "vs_ma200_pct",
                "golden_cross", "atr", "atr_pct"):
        if key in indicators:
            result[key] = indicators[key]
    return result


def quick_screen(tickers: list[str], top_n: int = 25) -> list[dict]:
    """
    Batch-download 20 days OHLCV for all tickers, compute technicals,
    return top_n ranked by tech_score.
    """
    try:
        raw = yf.download(
            tickers,
            period="60d",   # need 50d for MA50, 26d for MACD
            group_by="ticker",
            auto_adjust=True,
            threads=True,
            progress=False,
        )
    except Exception as e:
        print(f"[scanner] batch download error: {e}")
        return []

    results = []
    for symbol in tickers:
        try:
            if len(tickers) == 1:
                df = raw
            else:
                df = raw[symbol] if symbol in raw.columns.get_level_values(0) else pd.DataFrame()
            if df.empty or len(df) < 5:
                continue
            tech = compute_technicals(df)
            if not tech:
                continue
            # Filter: positive momentum, not overbought
            # volume_ratio used for ranking only (last bar may be partial intraday)
            if tech["momentum_5d"] > 0 and tech["rsi"] < 75:   # 75 = standard overbought threshold
                results.append({"symbol": symbol, **tech})
        except Exception:
            continue

    results.sort(key=lambda x: x["tech_score"], reverse=True)
    return results[:top_n]
