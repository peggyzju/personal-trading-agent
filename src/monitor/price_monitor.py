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
    """OHLCV 日线 — 走 Alpaca(feed=iex,替代 yfinance)。返回大写列名(Open/High/Low/Close/Volume)兼容 compute_all。"""
    from datetime import timedelta
    from src.trader.alpaca_trader import get_client
    days = {"30d": 55, "3mo": 100, "6mo": 190, "1y": 380}.get(period, 100)
    try:
        bars = get_client().get_bars(
            symbol, "1Day",
            start=(datetime.now() - timedelta(days=days)).date().isoformat(), feed="iex",
        ).df
    except Exception:
        return None
    if bars is None or len(bars) == 0:
        return None
    return bars.rename(columns={"open": "Open", "high": "High", "low": "Low", "close": "Close", "volume": "Volume"})
