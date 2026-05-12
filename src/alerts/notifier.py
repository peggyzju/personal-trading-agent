def console_alert(symbol: str, signal: str, price: float, reasoning: str):
    border = "=" * 60
    print(f"\n{border}")
    print(f"  TRADING SIGNAL: {symbol} — {signal}")
    print(f"  Price: ${price:.2f}")
    print(f"  {reasoning}")
    print(f"{border}\n")
