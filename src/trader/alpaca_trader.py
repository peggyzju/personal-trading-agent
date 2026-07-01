import os
import re
from typing import Optional
import alpaca_trade_api as tradeapi


def get_client():
    return tradeapi.REST(
        os.environ["ALPACA_API_KEY"],
        os.environ["ALPACA_SECRET_KEY"],
        os.environ.get("ALPACA_BASE_URL", "https://paper-api.alpaca.markets"),
    )


def _alpaca_symbol(symbol: str) -> str:
    """
    Normalize symbol to Alpaca format.
    yfinance/Wikipedia use hyphen for share classes (BRK-B, BF-B).
    Alpaca uses slash (BRK/B, BF/B).
    Pattern: trailing hyphen + 1-2 uppercase letters → replace with slash.
    """
    return re.sub(r'-([A-Z]{1,2})$', r'/\1', symbol)


def get_account():
    return get_client().get_account()


def get_position(symbol: str):
    api = get_client()
    try:
        return api.get_position(_alpaca_symbol(symbol))
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
        "symbol": _alpaca_symbol(symbol),
        "side": side,
        "type": order_type,
        # Fractional/notional orders require "day"; whole-share orders use "gtc" for pre/post market
        "time_in_force": "day" if notional is not None else "gtc",
    }
    if notional is not None:
        kwargs["notional"] = round(notional, 2)
    else:
        kwargs["qty"] = qty

    if order_type == "limit" and limit_price:
        kwargs["limit_price"] = str(round(limit_price, 2))
    if order_type in ("stop", "stop_limit") and stop_price:
        kwargs["stop_price"] = str(round(stop_price, 2))

    # Bracket order: attach stop-loss and/or take-profit legs.
    # Alpaca requires qty (not notional) for bracket orders — convert if needed.
    if stop_loss or take_profit:
        if kwargs.get("notional") is not None:
            # Convert dollar notional → whole share qty using current price
            notional_val = kwargs.pop("notional")
            try:
                # 用 Alpaca 取价（替代 yfinance，避免被 Yahoo 限流导致取价失败）
                live_price = float(api.get_latest_trade(symbol, feed="iex").price) or 0
            except Exception:
                live_price = 0
            if live_price > 0:
                import math
                computed_qty = math.floor(notional_val / live_price)
                if computed_qty >= 1:
                    kwargs["qty"] = computed_qty
                else:
                    # Price > notional — can't buy even 1 share; fall back to plain notional order
                    kwargs["notional"] = str(round(notional_val, 2))
                    stop_loss = None
                    take_profit = None
            else:
                # 取价失败：退化为普通 notional 市价单（无 bracket 止损），
                # 必须恢复 notional —— 否则缺 qty/notional 会被 Alpaca 拒单（曾导致买入静默失败）
                print(f"[alpaca] {symbol}: 无法取价，退化为普通 notional 单（无 bracket 止损，靠 holdings monitor 兜底）")
                kwargs["notional"] = str(round(notional_val, 2))
                stop_loss = None
                take_profit = None

        if stop_loss or take_profit:
            # bracket 止损必须 GTC —— 否则第 59 行按 notional 设的 "day" 会让子止损单
            # 当天收盘就 expired(每个自动买入的仓位当天丢服务端止损)。此处已转成整股 qty,
            # 整股单用 gtc(Alpaca 接受市价+gtc),止损单跨日持续有效。
            kwargs["time_in_force"] = "gtc"
            # bracket requires BOTH legs; oto (one-triggers-other) works with just one
            kwargs["order_class"] = "bracket" if (stop_loss and take_profit) else "oto"
            if stop_loss:
                kwargs["stop_loss"] = {"stop_price": str(round(stop_loss, 2))}
            if take_profit:
                kwargs["take_profit"] = {"limit_price": str(round(take_profit, 2))}

    return api.submit_order(**kwargs)


def close_position(symbol: str):
    """Close (liquidate) an entire position."""
    api = get_client()
    return api.close_position(_alpaca_symbol(symbol))


def cancel_order(order_id: str):
    """Cancel a pending order by ID."""
    api = get_client()
    return api.cancel_order(order_id)
