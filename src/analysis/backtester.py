from __future__ import annotations
import numpy as np
import pandas as pd
import yfinance as yf


# ── Signal logic (mirrors live strategy, no lookahead) ────────────────────────

def _precompute_signals(df: pd.DataFrame) -> pd.DataFrame:
    """
    Add all indicator columns using only past data (pandas rolling = no lookahead).
    Returns the same DataFrame with indicator columns appended.
    """
    c = df["Close"]
    v = df["Volume"]

    # RSI(14)
    delta = c.diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = (-delta.clip(upper=0)).rolling(14).mean()
    rs = gain / loss.replace(0, np.nan)
    df = df.copy()
    df["rsi"] = 100 - 100 / (1 + rs)

    # MACD(12,26,9)
    ema12 = c.ewm(span=12, adjust=False).mean()
    ema26 = c.ewm(span=26, adjust=False).mean()
    macd = ema12 - ema26
    macd_signal = macd.ewm(span=9, adjust=False).mean()
    df["macd_hist"] = macd - macd_signal

    # Bollinger %B (20,2)
    ma20 = c.rolling(20).mean()
    std20 = c.rolling(20).std()
    upper = ma20 + 2 * std20
    lower = ma20 - 2 * std20
    df["bb_pct_b"] = (c - lower) / (upper - lower).replace(0, np.nan)

    # Moving averages
    df["ma20"] = ma20
    df["ma50"] = c.rolling(50).mean()

    # ATR(14)
    prev_c = c.shift(1)
    tr = pd.concat([
        df["High"] - df["Low"],
        (df["High"] - prev_c).abs(),
        (df["Low"] - prev_c).abs(),
    ], axis=1).max(axis=1)
    df["atr"] = tr.rolling(14).mean()

    # Volume ratio (today vs 20-day avg)
    df["vol_ratio"] = v / v.rolling(20).mean()

    # 5-day momentum
    df["mom5"] = c.pct_change(5) * 100

    return df


def _buy_signal(row: pd.Series) -> bool:
    """True if all buy conditions are met at this row."""
    try:
        return (
            row["rsi"] < 75                    # not overbought (matches live scanner threshold)
            and row["macd_hist"] > 0           # bullish momentum
            and row["bb_pct_b"] > 0.55         # above BB midline — momentum continuation (matches scanner)
            and row["bb_pct_b"] < 0.90         # not at extreme upper band (avoid chasing)
            and row["Close"] > row["ma20"]     # above short-term trend
            and row["vol_ratio"] > 1.05        # slight volume confirmation
            and row["mom5"] > 0                # positive recent momentum
        )
    except Exception:
        return False


# ── Trade simulation ──────────────────────────────────────────────────────────

def _simulate_symbol(
    symbol: str,
    df: pd.DataFrame,
    hold_days: int,
    target_pct: float,
    slippage_pct: float = 0.003,   # 0.3% — realistic for liquid mid/large caps (was 0.1%)
) -> list[dict]:
    """Walk-forward simulation for one symbol. Returns list of trade dicts."""
    df = _precompute_signals(df)
    df = df.dropna(subset=["rsi", "macd_hist", "ma50", "atr"])

    trades = []
    in_trade = False
    entry_price = stop_loss = target = 0.0
    entry_date = entry_idx = None
    atr_at_entry = 0.0

    rows = list(df.itertuples())

    for i, row in enumerate(rows):
        if not in_trade:
            if _buy_signal(row):
                # Enter next bar's open (simulate realistic execution)
                if i + 1 >= len(rows):
                    continue
                next_row = rows[i + 1]
                entry_price = float(next_row.Open) * (1 + slippage_pct)
                atr_at_entry = float(row.atr)
                stop_loss = entry_price - 2 * atr_at_entry
                target = entry_price * (1 + target_pct)
                entry_date = next_row.Index
                entry_idx = i + 1
                in_trade = True
        else:
            days_held = i - entry_idx
            low = float(row.Low)
            high = float(row.High)
            close = float(row.Close)

            exit_price = None
            exit_reason = None

            if low <= stop_loss:
                exit_price = stop_loss * (1 - slippage_pct)
                exit_reason = "stop_loss"
            elif high >= target:
                exit_price = target * (1 - slippage_pct)
                exit_reason = "target_hit"
            elif days_held >= hold_days:
                exit_price = close * (1 - slippage_pct)
                exit_reason = "time_exit"

            if exit_price is not None:
                pnl_pct = (exit_price - entry_price) / entry_price * 100
                trades.append({
                    "symbol": symbol,
                    "entry_date": str(entry_date.date()) if hasattr(entry_date, "date") else str(entry_date),
                    "exit_date": str(row.Index.date()) if hasattr(row.Index, "date") else str(row.Index),
                    "entry_price": round(entry_price, 2),
                    "exit_price": round(exit_price, 2),
                    "pnl_pct": round(pnl_pct, 2),
                    "exit_reason": exit_reason,
                    "days_held": days_held,
                    "atr_at_entry": round(atr_at_entry, 2),
                })
                in_trade = False

    return trades


# ── Stats ─────────────────────────────────────────────────────────────────────

def _compute_stats(trades: list[dict], spy_return: float) -> dict:
    if not trades:
        return {"error": "no_trades"}

    pnls = [t["pnl_pct"] for t in trades]
    winners = [p for p in pnls if p > 0]
    losers  = [p for p in pnls if p <= 0]

    win_rate = len(winners) / len(pnls) * 100
    avg_win  = float(np.mean(winners)) if winners else 0.0
    avg_loss = float(np.mean(losers))  if losers  else 0.0
    profit_factor = (
        abs(sum(winners) / sum(losers)) if sum(losers) != 0 else float("inf")
    )

    # Cumulative equity curve (equal-weight, each trade = 1 unit)
    equity = [100.0]
    for p in pnls:
        equity.append(equity[-1] * (1 + p / 100))

    # Max drawdown
    peak = equity[0]
    max_dd = 0.0
    for e in equity:
        if e > peak:
            peak = e
        dd = (peak - e) / peak * 100
        if dd > max_dd:
            max_dd = dd

    # Sharpe (annualise assuming ~252 trading days / year, avg hold ~hold_days)
    if len(pnls) > 1:
        returns_arr = np.array(pnls)
        avg_hold = float(np.mean([t["days_held"] for t in trades])) if trades else 10.0
        ann_factor = np.sqrt(252 / max(avg_hold, 1))   # annualise using actual avg hold period
        sharpe = float(
            np.mean(returns_arr) / np.std(returns_arr) * ann_factor
        ) if np.std(returns_arr) > 0 else 0.0
    else:
        sharpe = 0.0

    total_return = equity[-1] - 100

    # Exit breakdown
    reasons: dict[str, int] = {}
    for t in trades:
        r = t["exit_reason"]
        reasons[r] = reasons.get(r, 0) + 1

    return {
        "total_trades": len(trades),
        "win_rate": round(win_rate, 1),
        "avg_win_pct": round(avg_win, 2),
        "avg_loss_pct": round(avg_loss, 2),
        "profit_factor": round(profit_factor, 2),
        "total_return_pct": round(total_return, 2),
        "spy_return_pct": round(spy_return, 2),
        "alpha_pct": round(total_return - spy_return, 2),
        "max_drawdown_pct": round(max_dd, 2),
        "sharpe_ratio": round(sharpe, 2),
        "exit_breakdown": reasons,
        "equity_curve": [round(e, 2) for e in equity],
        "trades": sorted(trades, key=lambda t: t["entry_date"]),
    }


# ── Public entry point ────────────────────────────────────────────────────────

def run_backtest(
    symbols: list[str],
    period: str = "2y",
    hold_days: int = 10,
    target_pct: float = 0.08,
) -> dict:
    """
    Run walk-forward backtest on given symbols.
    Returns stats dict with equity_curve and trades list.
    """
    print(f"[backtest] Downloading {len(symbols)} symbols, period={period}…")

    # Download all symbols + SPY in one batch
    all_syms = list(set(symbols + ["SPY"]))
    try:
        raw = yf.download(
            all_syms,
            period=period,
            group_by="ticker",
            auto_adjust=True,
            threads=True,
            progress=False,
        )
    except Exception as e:
        return {"error": str(e)}

    def _get_df(sym: str) -> pd.DataFrame:
        if len(all_syms) == 1:
            return raw
        lvl0 = raw.columns.get_level_values(0)
        return raw[sym] if sym in lvl0 else pd.DataFrame()

    # SPY buy-and-hold return for the period
    spy_df = _get_df("SPY")
    if not spy_df.empty:
        spy_close = spy_df["Close"].dropna()
        spy_return = (float(spy_close.iloc[-1]) / float(spy_close.iloc[0]) - 1) * 100
    else:
        spy_return = 0.0

    # Simulate each symbol
    all_trades: list[dict] = []
    for sym in symbols:
        try:
            df = _get_df(sym)
            if df.empty or len(df) < 60:
                print(f"[backtest] {sym}: insufficient data, skipping")
                continue
            trades = _simulate_symbol(sym, df, hold_days, target_pct)
            print(f"[backtest] {sym}: {len(trades)} trades")
            all_trades.extend(trades)
        except Exception as e:
            print(f"[backtest] {sym} error: {e}")

    return _compute_stats(all_trades, spy_return)
