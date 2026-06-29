import yfinance as yf
from datetime import datetime


def get_quote(symbol: str) -> dict:
    """实时报价 — 走 Alpaca 日线(feed=iex,根治 yfinance fast_info 限流卡死)。"""
    from datetime import timedelta
    from src.trader.alpaca_trader import get_client
    bars = get_client().get_bars(
        symbol, "1Day",
        start=(datetime.now() - timedelta(days=8)).date().isoformat(), feed="iex",
    ).df
    if bars is None or len(bars) == 0:
        raise ValueError(f"no Alpaca bars for {symbol}")
    closes = bars["close"].tolist()
    vols = bars["volume"].tolist()
    price = float(closes[-1])
    prev = float(closes[-2]) if len(closes) >= 2 else price
    return {
        "symbol": symbol,
        "price": price,
        "prev_close": prev,
        "change_pct": (price - prev) / prev * 100 if prev else 0.0,
        "volume": int(vols[-1]) if vols else 0,
        "timestamp": datetime.utcnow().isoformat(),
    }


def get_ohlcv(symbol: str, period: str = "3mo", interval: str = "1d"):
    ticker = yf.Ticker(symbol)
    return ticker.history(period=period, interval=interval)
