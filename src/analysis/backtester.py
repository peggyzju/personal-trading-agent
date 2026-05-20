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


def _buy_signal(row) -> bool:
    """True if all buy conditions are met at this row.
    Works with both pd.Series (bracket access) and itertuples namedtuples (dot access).
    """
    try:
        # Support both Series and namedtuple (itertuples)
        def g(field):
            try:
                return row[field]
            except (KeyError, TypeError):
                return getattr(row, field)
        return (
            g("rsi") < 75                    # not overbought
            and g("macd_hist") > 0           # bullish momentum
            and g("bb_pct_b") > 0.55         # above BB midline
            and g("bb_pct_b") < 0.90         # not at extreme upper band
            and g("Close") > g("ma20")       # above short-term trend
            and g("vol_ratio") > 1.05        # slight volume confirmation
            and g("mom5") > 0                # positive recent momentum
        )
    except Exception:
        return False


# ── Trade simulation ──────────────────────────────────────────────────────────

def _buy_signal_strict(row) -> bool:
    """Stricter entry: same as _buy_signal but RSI < 60 and price within 5% above MA20."""
    try:
        def g(field):
            try:
                return row[field]
            except (KeyError, TypeError):
                return getattr(row, field)
        return (
            g("rsi") < 60                    # not extended (tighter than 75)
            and g("macd_hist") > 0
            and g("bb_pct_b") > 0.45         # slightly above midline
            and g("bb_pct_b") < 0.85
            and g("Close") > g("ma20")
            and g("Close") < g("ma20") * 1.05  # within 5% of MA20 (near support)
            and g("vol_ratio") > 1.10        # stronger volume confirmation
            and g("mom5") > 0
        )
    except Exception:
        return False


def _simulate_symbol(
    symbol: str,
    df: pd.DataFrame,
    hold_days: int,
    target_pct: float,
    slippage_pct: float = 0.003,
    stop_type: str = "atr",      # "atr" | "fixed_3pct" | "fixed_5pct"
    entry_mode: str = "normal",  # "normal" | "strict"
) -> list[dict]:
    """Walk-forward simulation for one symbol. Returns list of trade dicts."""
    df = _precompute_signals(df)
    df = df.dropna(subset=["rsi", "macd_hist", "ma50", "atr"])

    signal_fn = _buy_signal_strict if entry_mode == "strict" else _buy_signal

    trades = []
    in_trade = False
    entry_price = stop_loss = target = 0.0
    entry_date = entry_idx = None
    atr_at_entry = 0.0

    rows = list(df.itertuples())

    for i, row in enumerate(rows):
        if not in_trade:
            if signal_fn(row):
                # Enter next bar's open (simulate realistic execution)
                if i + 1 >= len(rows):
                    continue
                next_row = rows[i + 1]
                entry_price = float(next_row.Open) * (1 + slippage_pct)
                atr_at_entry = float(row.atr)
                if stop_type == "fixed_3pct":
                    stop_loss = entry_price * 0.97
                elif stop_type == "fixed_5pct":
                    stop_loss = entry_price * 0.95
                else:  # atr
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

_INITIAL_CAPITAL = 100_000.0
_RISK_PCT        = 0.02        # 2% portfolio risk per trade (matches live strategy)
_MAX_POS_PCT     = 0.12        # cap single position at 12% of capital


def _compute_stats(trades: list[dict], spy_return: float) -> dict:
    if not trades:
        return {"error": "no_trades"}

    # ── ATR-based position sizing ──────────────────────────────────────────────
    # Mirrors live strategy: risk 2% of capital on a 2×ATR stop.
    # Dollar P&L replaces raw pnl_pct for equity curve — fixes sequential-
    # compounding bias when multiple symbols are tested together.
    for t in trades:
        atr   = t.get("atr_at_entry", 0)
        entry = t.get("entry_price", 1) or 1
        if atr > 0:
            shares   = (_INITIAL_CAPITAL * _RISK_PCT) / (2 * atr)
            notional = min(shares * entry, _INITIAL_CAPITAL * _MAX_POS_PCT)
        else:
            notional = _INITIAL_CAPITAL * 0.05   # 5% fallback for missing ATR
        t["notional"]   = round(notional, 2)
        t["dollar_pnl"] = round(notional * t["pnl_pct"] / 100, 2)

    pnls = [t["pnl_pct"] for t in trades]
    winners = [p for p in pnls if p > 0]
    losers  = [p for p in pnls if p <= 0]

    win_rate      = len(winners) / len(pnls) * 100
    avg_win       = float(np.mean(winners)) if winners else 0.0
    avg_loss      = float(np.mean(losers))  if losers  else 0.0
    profit_factor = abs(sum(winners) / sum(losers)) if sum(losers) != 0 else float("inf")

    # ── Portfolio equity curve ────────────────────────────────────────────────
    # Sorted by exit_date so the curve reflects capital as each trade closes.
    # Multiple concurrent positions are approximated (not intra-day NAV).
    sorted_by_exit = sorted(trades, key=lambda t: t["exit_date"])
    capital = _INITIAL_CAPITAL
    equity_abs = [capital]
    for t in sorted_by_exit:
        capital += t["dollar_pnl"]
        equity_abs.append(capital)

    # Normalise to 100 for display
    equity = [round(v / _INITIAL_CAPITAL * 100, 2) for v in equity_abs]

    # ── Max drawdown (on normalised curve) ───────────────────────────────────
    peak   = equity[0]
    max_dd = 0.0
    for e in equity:
        if e > peak:
            peak = e
        dd = (peak - e) / peak * 100
        if dd > max_dd:
            max_dd = dd

    # ── Sharpe ratio (per-trade approximation, annualised) ───────────────────
    # Uses per-trade pnl_pct, not daily returns — treats each trade as one
    # observation. Annualised via average holding period.
    if len(pnls) > 1:
        returns_arr = np.array(pnls)
        avg_hold    = float(np.mean([t["days_held"] for t in trades])) or 10.0
        ann_factor  = np.sqrt(252 / max(avg_hold, 1))
        sharpe = float(
            np.mean(returns_arr) / np.std(returns_arr) * ann_factor
        ) if np.std(returns_arr) > 0 else 0.0
    else:
        sharpe = 0.0

    total_return = equity[-1] - 100

    # ── Exit breakdown ────────────────────────────────────────────────────────
    reasons: dict[str, int] = {}
    for t in trades:
        r = t["exit_reason"]
        reasons[r] = reasons.get(r, 0) + 1

    return {
        "total_trades":     len(trades),
        "win_rate":         round(win_rate, 1),
        "avg_win_pct":      round(avg_win, 2),
        "avg_loss_pct":     round(avg_loss, 2),
        "profit_factor":    round(profit_factor, 2),
        "total_return_pct": round(total_return, 2),
        "spy_return_pct":   round(spy_return, 2),
        "alpha_pct":        round(total_return - spy_return, 2),
        "max_drawdown_pct": round(max_dd, 2),
        "sharpe_ratio":     round(sharpe, 2),
        "exit_breakdown":   reasons,
        "equity_curve":     equity,
        "trades":           sorted(trades, key=lambda t: t["entry_date"]),
    }


# ── Public entry point ────────────────────────────────────────────────────────

def _download_data(symbols: list[str], period: str) -> tuple:
    """Download price data for symbols + SPY. Returns (raw, get_df, spy_return)."""
    all_syms = list(set(symbols + ["SPY"]))
    raw = yf.download(
        all_syms,
        period=period,
        group_by="ticker",
        auto_adjust=True,
        threads=True,
        progress=False,
    )

    def _get_df(sym: str) -> pd.DataFrame:
        if len(all_syms) == 1:
            return raw
        lvl0 = raw.columns.get_level_values(0)
        return raw[sym] if sym in lvl0 else pd.DataFrame()

    spy_df = _get_df("SPY")
    spy_return = 0.0
    if not spy_df.empty:
        spy_close = spy_df["Close"].dropna()
        spy_return = (float(spy_close.iloc[-1]) / float(spy_close.iloc[0]) - 1) * 100

    return _get_df, spy_return


def run_backtest(
    symbols: list[str],
    period: str = "2y",
    hold_days: int = 10,
    target_pct: float = 0.08,
    stop_type: str = "atr",
    entry_mode: str = "normal",
) -> dict:
    """
    Run walk-forward backtest on given symbols.
    stop_type: "atr" | "fixed_3pct" | "fixed_5pct"
    entry_mode: "normal" | "strict"
    Returns stats dict with equity_curve and trades list.
    """
    print(f"[backtest] {len(symbols)} symbols, period={period}, stop={stop_type}, entry={entry_mode}…")
    try:
        _get_df, spy_return = _download_data(symbols, period)
    except Exception as e:
        return {"error": str(e)}

    all_trades: list[dict] = []
    for sym in symbols:
        try:
            df = _get_df(sym)
            if df.empty or len(df) < 60:
                continue
            trades = _simulate_symbol(sym, df, hold_days, target_pct,
                                      stop_type=stop_type, entry_mode=entry_mode)
            print(f"[backtest] {sym}: {len(trades)} trades")
            all_trades.extend(trades)
        except Exception as e:
            print(f"[backtest] {sym} error: {e}")

    return _compute_stats(all_trades, spy_return)


def compare_strategies(
    symbols: list[str],
    period: str = "1y",
    hold_days: int = 10,
    target_pct: float = 0.08,
) -> dict:
    """
    Run 3 scenarios and return side-by-side comparison:
    A: current live (fixed 3% stop, normal entry)
    B: wider stop  (fixed 5% stop, normal entry)
    C: strict entry (fixed 3% stop, strict entry RSI<60 + near MA)
    """
    print(f"[backtest] Downloading data for comparison ({len(symbols)} symbols)…")
    try:
        _get_df, spy_return = _download_data(symbols, period)
    except Exception as e:
        return {"error": str(e)}

    results = {}
    configs = [
        ("current_3pct",  "fixed_3pct", "normal"),
        ("wider_5pct",    "fixed_5pct", "normal"),
        ("strict_entry",  "fixed_3pct", "strict"),
        ("combined_ab",   "fixed_5pct", "strict"),   # A+B combined
    ]
    for label, stop, entry in configs:
        trades = []
        for sym in symbols:
            try:
                df = _get_df(sym)
                if df.empty or len(df) < 60:
                    continue
                trades.extend(_simulate_symbol(sym, df, hold_days, target_pct,
                                               stop_type=stop, entry_mode=entry))
            except Exception:
                pass
        stats = _compute_stats(trades, spy_return)
        results[label] = {
            "total_trades":     stats.get("total_trades"),
            "win_rate":         stats.get("win_rate"),
            "avg_win_pct":      stats.get("avg_win_pct"),
            "avg_loss_pct":     stats.get("avg_loss_pct"),
            "profit_factor":    stats.get("profit_factor"),
            "total_return_pct": stats.get("total_return_pct"),
            "sharpe_ratio":     stats.get("sharpe_ratio"),
            "max_drawdown_pct": stats.get("max_drawdown_pct"),
            "exit_breakdown":   stats.get("exit_breakdown"),
            "equity_curve":     stats.get("equity_curve"),
        }
        print(f"[backtest] {label}: {stats.get('total_trades')} trades, "
              f"win={stats.get('win_rate')}%, pf={stats.get('profit_factor')}, "
              f"ret={stats.get('total_return_pct')}%")

    results["spy_return_pct"] = spy_return
    results["period"] = period
    results["symbols"] = symbols
    results["status"] = "done"
    return results
