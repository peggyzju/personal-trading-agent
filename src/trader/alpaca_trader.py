import os
import alpaca_trade_api as tradeapi


def get_client():
    return tradeapi.REST(
        os.environ["ALPACA_API_KEY"],
        os.environ["ALPACA_SECRET_KEY"],
        os.environ.get("ALPACA_BASE_URL", "https://paper-api.alpaca.markets"),
    )


def get_position(symbol: str):
    api = get_client()
    try:
        return api.get_position(symbol)
    except Exception:
        return None


def place_order(symbol: str, side: str, qty: int, order_type: str = "market"):
    api = get_client()
    return api.submit_order(
        symbol=symbol,
        qty=qty,
        side=side,
        type=order_type,
        time_in_force="day",
    )


def get_account():
    return get_client().get_account()
