import yfinance as yf
from datetime import datetime


def get_quote(symbol: str) -> dict:
    ticker = yf.Ticker(symbol)
    info = ticker.fast_info
    return {
        "symbol": symbol,
        "price": info.last_price,
        "prev_close": info.previous_close,
        "change_pct": (info.last_price - info.previous_close) / info.previous_close * 100,
        "volume": info.three_month_average_volume,
        "timestamp": datetime.utcnow().isoformat(),
    }


def get_ohlcv(symbol: str, period: str = "3mo", interval: str = "1d"):
    ticker = yf.Ticker(symbol)
    return ticker.history(period=period, interval=interval)
