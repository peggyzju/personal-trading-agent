import os
from typing import Optional
import alpaca_trade_api as tradeapi


def get_client():
    return tradeapi.REST(
        os.environ["ALPACA_API_KEY"],
        os.environ["ALPACA_SECRET_KEY"],
        os.environ.get("ALPACA_BASE_URL", "https://paper-api.alpaca.markets"),
    )


def get_account():
    return get_client().get_account()


def get_position(symbol: str):
    api = get_client()
    try:
        return api.get_position(symbol)
    except Exception:
        return None


def place_order(
    symbol: str,
    side: str,
    qty: Optional[float] = None,
    notional: Optional[float] = None,
    order_type: str = "market",
    limit_price: Optional[float] = None,
    stop_price: Optional[float] = None,
    stop_loss: Optional[float] = None,    # bracket: stop-loss price
    take_profit: Optional[float] = None,  # bracket: take-profit limit price
):
    """
    Place a buy or sell order.
    - Provide qty (shares) OR notional (dollars), not both.
    - Provide stop_loss + take_profit to submit a bracket order (OTO).
    """
    api = get_client()
    kwargs: dict = {
        "symbol": symbol,
        "side": side,
        "type": order_type,
        "time_in_force": "day",
    }
    if notional is not None:
        kwargs["notional"] = round(notional, 2)
    else:
        kwargs["qty"] = qty

    if order_type == "limit" and limit_price:
        kwargs["limit_price"] = str(round(limit_price, 2))
    if order_type in ("stop", "stop_limit") and stop_price:
        kwargs["stop_price"] = str(round(stop_price, 2))

    # Bracket order: attach stop-loss and/or take-profit legs
    if stop_loss or take_profit:
        kwargs["order_class"] = "bracket"
        if stop_loss:
            kwargs["stop_loss"] = {"stop_price": str(round(stop_loss, 2))}
        if take_profit:
            kwargs["take_profit"] = {"limit_price": str(round(take_profit, 2))}

    return api.submit_order(**kwargs)


def close_position(symbol: str):
    """Close (liquidate) an entire position."""
    api = get_client()
    return api.close_position(symbol)


def cancel_order(order_id: str):
    """Cancel a pending order by ID."""
    api = get_client()
    return api.cancel_order(order_id)
