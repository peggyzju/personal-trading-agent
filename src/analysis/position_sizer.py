from __future__ import annotations


DEFAULT_RISK_PCT = 0.02    # risk 2% of portfolio per trade
DEFAULT_MAX_PCT  = 0.10    # max 10% of portfolio in one position
MAX_POSITIONS    = 10      # max concurrent open positions


def compute_structured_stop(
    entry_price: float,
    ma20: float | None,
    atr: float | None,
    min_stop_pct: float = 0.03,   # floor: never tighter than 3%
    max_stop_pct: float = 0.08,   # ceiling: never wider than 8%
) -> float:
    """
    Structural stop: max(MA20 × 0.99,  entry − 1.5×ATR)

    Places the stop below the nearest structural support rather than using
    a fixed percentage, so high-ATR stocks get wider stops (smaller position)
    and low-ATR stocks get tighter stops (larger position) — all while
    keeping per-trade risk locked at 2% of portfolio.

    Always clamped:
      floor   = entry × (1 − max_stop_pct)   → never wider than 8%
      ceiling = entry × (1 − min_stop_pct)   → never tighter than 3%
    """
    if not entry_price or entry_price <= 0:
        return 0.0

    candidates: list[float] = []
    if ma20 and ma20 > 0:
        candidates.append(ma20 * 0.99)              # 1% below MA20 support
    if atr and atr > 0:
        candidates.append(entry_price - 1.5 * atr)  # 1.5×ATR cushion

    raw_stop = max(candidates) if candidates else entry_price * (1 - min_stop_pct)

    # Clamp between floor (widest allowed) and ceiling (tightest allowed)
    floor   = entry_price * (1 - max_stop_pct)
    ceiling = entry_price * (1 - min_stop_pct)
    return round(max(floor, min(raw_stop, ceiling)), 2)


def size_position(
    portfolio_value: float,
    price: float,
    stop_loss: float,
    risk_pct: float = DEFAULT_RISK_PCT,
    max_pct: float = DEFAULT_MAX_PCT,
) -> dict:
    """
    Fixed-fractional position sizing.
    shares = (portfolio * risk_pct) / (price - stop_loss)
    Capped at max_pct of portfolio.
    """
    price_risk = price - stop_loss
    if price_risk <= 0 or portfolio_value <= 0:
        return {"shares": 0, "cost": 0, "max_loss": 0, "portfolio_pct": 0, "risk_pct_actual": 0}

    shares_by_risk = int((portfolio_value * risk_pct) / price_risk)
    shares_by_cap  = int((portfolio_value * max_pct) / price)
    shares = min(shares_by_risk, shares_by_cap)   # 0 is valid — skip if stock is too expensive

    if shares <= 0:
        return {"shares": 0, "cost": 0, "max_loss": 0, "portfolio_pct": 0, "risk_pct_actual": 0}

    cost = round(shares * price, 2)
    max_loss = round(shares * price_risk, 2)

    return {
        "shares": shares,
        "cost": cost,
        "max_loss": max_loss,
        "portfolio_pct": round(cost / portfolio_value * 100, 1),
        "risk_pct_actual": round(max_loss / portfolio_value * 100, 2),
    }


def build_allocation_summary(
    portfolio_value: float,
    cash: float,
    positions: list[dict],
    candidates: list[dict],
    risk_pct: float = DEFAULT_RISK_PCT,
    max_pct: float = DEFAULT_MAX_PCT,
) -> dict:
    """
    Full portfolio allocation breakdown.
    positions: list of {symbol, market_value, unrealized_pl, unrealized_plpc}
    candidates: list of {symbol, price, stop_loss}
    """
    invested = portfolio_value - cash
    cash_pct = round(cash / portfolio_value * 100, 1) if portfolio_value else 0

    # current holdings breakdown
    holdings = [
        {
            "symbol": p["symbol"],
            "market_value": p["market_value"],
            "pct": round(p["market_value"] / portfolio_value * 100, 1),
            "unrealized_pl": p["unrealized_pl"],
            "unrealized_plpc": p["unrealized_plpc"],
        }
        for p in positions
    ]

    # suggested buys with position sizing
    slots_remaining = max(0, MAX_POSITIONS - len(positions))
    capital_per_slot = (cash * 0.9) / slots_remaining if slots_remaining > 0 else 0

    suggested_buys = []
    for c in candidates[:slots_remaining]:
        price = c.get("price", 0)
        stop_loss = c.get("stop_loss", price * 0.97)
        sizing = size_position(portfolio_value, price, stop_loss, risk_pct, max_pct)
        # further cap by available capital per slot
        if capital_per_slot > 0 and sizing["cost"] > capital_per_slot:
            capped_shares = max(1, int(capital_per_slot / price))
            sizing = size_position(portfolio_value, price,
                                   stop_loss, risk_pct,
                                   min(max_pct, capital_per_slot / portfolio_value))
        suggested_buys.append({
            "symbol": c["symbol"],
            "signal": c.get("signal", "BUY"),
            "ai_score": c.get("ai_score", 0),
            "price": price,
            "stop_loss": stop_loss,
            "target_price": c.get("target_price", 0),
            "reason": c.get("reason", ""),
            **sizing,
        })

    total_suggested_cost = sum(b["cost"] for b in suggested_buys)

    return {
        "portfolio_value": round(portfolio_value, 2),
        "cash": round(cash, 2),
        "invested": round(invested, 2),
        "cash_pct": cash_pct,
        "invested_pct": round(100 - cash_pct, 1),
        "slots_remaining": slots_remaining,
        "risk_per_trade_pct": round(risk_pct * 100, 1),
        "max_position_pct": round(max_pct * 100, 1),
        "holdings": holdings,
        "suggested_buys": suggested_buys,
        "total_suggested_cost": round(total_suggested_cost, 2),
    }
