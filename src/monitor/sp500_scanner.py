from __future__ import annotations
import pandas as pd
import yfinance as yf
import numpy as np


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
    """Compute momentum, volume ratio, RSI(14) from OHLCV DataFrame."""
    closes = df["Close"].dropna()
    volumes = df["Volume"].dropna()
    if len(closes) < 6:
        return {}

    price_now = float(closes.iloc[-1])
    price_5d = float(closes.iloc[-6]) if len(closes) >= 6 else float(closes.iloc[0])
    momentum_5d = (price_now - price_5d) / price_5d * 100

    vol_today = float(volumes.iloc[-1])
    vol_avg = float(volumes.iloc[-21:-1].mean()) if len(volumes) >= 21 else float(volumes.mean())
    volume_ratio = vol_today / vol_avg if vol_avg > 0 else 1.0

    high_20d = float(closes.iloc[-21:-1].max()) if len(closes) >= 21 else float(closes.max())
    near_breakout = price_now >= high_20d * 0.98

    # RSI(14)
    delta = closes.diff().dropna()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = (-delta.clip(upper=0)).rolling(14).mean()
    rs = gain / loss.replace(0, np.nan)
    rsi = float((100 - 100 / (1 + rs)).iloc[-1]) if not rs.isna().all() else 50.0

    score = (
        min(max(momentum_5d, 0), 10) * 0.35
        + min(max(volume_ratio - 1, 0), 3) * 10 * 0.25
        + (20 if near_breakout else 0) * 0.25
        + max(0, (rsi - 50)) * 0.15
    )

    return {
        "price": round(price_now, 2),
        "momentum_5d": round(momentum_5d, 2),
        "volume_ratio": round(volume_ratio, 2),
        "near_breakout": near_breakout,
        "rsi": round(rsi, 1),
        "tech_score": round(score, 2),
    }


def quick_screen(tickers: list[str], top_n: int = 25) -> list[dict]:
    """
    Batch-download 20 days OHLCV for all tickers, compute technicals,
    return top_n ranked by tech_score.
    """
    try:
        raw = yf.download(
            tickers,
            period="20d",
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
            # Filter: positive momentum, volume spike, not overbought
            if tech["momentum_5d"] > 0 and tech["volume_ratio"] > 1.1 and tech["rsi"] < 75:
                results.append({"symbol": symbol, **tech})
        except Exception:
            continue

    results.sort(key=lambda x: x["tech_score"], reverse=True)
    return results[:top_n]
