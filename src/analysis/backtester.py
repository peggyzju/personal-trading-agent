from __future__ import annotations
import numpy as np
import pandas as pd
import yfinance as yf


# ── Signal logic (mirrors live strategy, no lookahead) ────────────────────────

def _precompute_signals(df: pd.DataFrame, spy_close: "pd.Series | None" = None) -> pd.DataFrame:
    """
    Add all indicator columns using only past data (pandas rolling = no lookahead).
    spy_close: optional SPY Close series aligned to same index, for RS score.
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

    # MA20 slope: 5-day change in MA20 value (mirrors scanner logic)
    df["ma20_slope"] = (ma20 - ma20.shift(5)) / ma20.shift(5).replace(0, np.nan) * 100

    # Relative strength vs SPY (rs_score > 1 = outperforming)
    if spy_close is not None:
        spy = spy_close.reindex(df.index, method="ffill")
        stock_ret_20 = c.pct_change(20)
        stock_ret_60 = c.pct_change(60)
        spy_ret_20   = spy.pct_change(20)
        spy_ret_60   = spy.pct_change(60)
        df["rs_20"]    = (1 + stock_ret_20) / (1 + spy_ret_20).replace(0, np.nan)
        df["rs_60"]    = (1 + stock_ret_60) / (1 + spy_ret_60).replace(0, np.nan)
        df["rs_score"] = df["rs_20"] * 0.4 + df["rs_60"] * 0.6
    else:
        df["rs_20"] = df["rs_60"] = df["rs_score"] = np.nan

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


def _buy_signal_dual_track(row) -> str | None:
    """Dual-track entry (v5):
    Returns 'track1', 'track2', or None.
    Track 1 — Momentum Breakout: RSI 50-75, above MA20, positive momentum
    Track 2 — Compression Coil: RSI<55, low volume, mild momentum
    """
    try:
        def g(field):
            try:
                return row[field]
            except (KeyError, TypeError):
                return getattr(row, field)
        rsi     = g("rsi")
        macd    = g("macd_hist")
        close   = g("Close")
        ma20    = g("ma20")
        vol     = g("vol_ratio")
        mom5    = g("mom5")
        vs_ma20 = (close - ma20) / ma20 * 100 if ma20 > 0 else 0

        if (50 <= rsi <= 75 and close > ma20 and mom5 > 0 and vs_ma20 <= 15.0 and macd > 0):
            return "track1"
        if (rsi < 55 and vol < 0.8 and mom5 > -3 and macd > 0 and vs_ma20 > -8.0):
            return "track2"
        return None
    except Exception:
        return None


def _buy_signal_dual_track_v6(row) -> str | None:
    """Dual-track entry (v6):
    Track 1 — Momentum Breakout (with vol gate):
        RSI 50-75, vol_ratio ≥ 1.5, above MA20, vs_ma20 ≤ 15%, mom5 > 0
    Track 2 — Compression Coil (with MA20 slope gate):
        RSI < 55, vol_ratio < 0.8, mom5 > -3, vs_ma20 ≥ -3.0, ma20_slope > 0
    """
    try:
        def g(field):
            try:
                return row[field]
            except (KeyError, TypeError):
                return getattr(row, field)
        rsi        = g("rsi")
        close      = g("Close")
        ma20       = g("ma20")
        vol        = g("vol_ratio")
        mom5       = g("mom5")
        try:
            ma20_slope = g("ma20_slope")
        except Exception:
            ma20_slope = float("nan")
        vs_ma20 = (close - ma20) / ma20 * 100 if ma20 > 0 else 0.0

        # Track 1: momentum breakout with volume confirmation
        if (50 <= rsi <= 75 and close > ma20 and mom5 > 0
                and vs_ma20 <= 15.0 and not pd.isna(vol) and vol >= 1.5):
            return "track1"
        # Track 2: compression coil with slope gate (not dead water)
        if (rsi < 55 and not pd.isna(vol) and vol < 0.8 and mom5 > -3
                and vs_ma20 >= -3.0
                and not pd.isna(ma20_slope) and ma20_slope > 0):
            return "track2"
        return None
    except Exception:
        return None


def _buy_signal_rs_momentum(row) -> bool:
    """Relative-strength momentum entry:
    Stock outperforms SPY on both 20d and 60d, price above MA20, slight volume confirmation.
    Intentionally simple — only 3 conditions, no RSI/MACD/BB clutter.
    """
    try:
        def g(f):
            try: return row[f]
            except (KeyError, TypeError): return getattr(row, f)
        rs_score  = g("rs_score")
        close     = g("Close")
        ma20      = g("ma20")
        vol_ratio = g("vol_ratio")
        rs_20     = g("rs_20")
        if pd.isna(rs_score) or pd.isna(rs_20):
            return False
        return (
            rs_score  > 1.05   # outperforming SPY composite score
            and rs_20 > 1.0    # must be outperforming on 20d too (recent strength)
            and close > ma20   # above short-term trend
            and vol_ratio > 1.1  # mild volume confirmation
        )
    except Exception:
        return False


def _simulate_symbol(
    symbol: str,
    df: pd.DataFrame,
    hold_days: int,
    target_pct: float,
    slippage_pct: float = 0.003,
    stop_type: str = "atr",
    entry_mode: str = "normal",
    exit_mode: str = "fixed",
    trail_pct: float = 0.08,
    trail_trigger: float = 0.08,
    trail_trigger_t1: float | None = None,
    trail_trigger_t2: float | None = None,
    spy_gate: bool = False,          # Gate A: block Track1 when SPY < MA20
    rr_min: float = 0.0,             # Gate B: skip entry if R:R < rr_min (Track2 / legacy)
    max_stop_t1: float | None = None, # Gate B v6: max stop distance for Track1 (e.g. 0.08)
    spy_df: "pd.DataFrame | None" = None,  # SPY Close + ma20 indexed by date
) -> list[dict]:
    """Walk-forward simulation for one symbol. Returns list of trade dicts."""
    spy_close = spy_df["Close"] if spy_df is not None else None
    df = _precompute_signals(df, spy_close=spy_close)
    df = df.dropna(subset=["rsi", "macd_hist", "ma50", "atr"])

    if entry_mode == "dual_track_v6":
        signal_fn = _buy_signal_dual_track_v6
    elif entry_mode == "dual_track":
        signal_fn = _buy_signal_dual_track
    elif entry_mode == "strict":
        signal_fn = _buy_signal_strict
    elif entry_mode == "rs_momentum":
        signal_fn = _buy_signal_rs_momentum
    else:
        signal_fn = _buy_signal

    # T1/T2 split only when explicitly specified in params
    _use_split = (trail_trigger_t1 is not None and trail_trigger_t2 is not None)
    TRAIL_TRIGGER_T1 = trail_trigger_t1 if _use_split else trail_trigger
    TRAIL_TRIGGER_T2 = trail_trigger_t2 if _use_split else trail_trigger

    trades = []
    in_trade = False
    entry_price = stop_loss = target = 0.0
    entry_date = entry_idx = None
    atr_at_entry = 0.0
    high_water = 0.0
    trailing_active = False
    active_trail_trigger = trail_trigger  # per-trade, overridden for dual_track
    cooldown_until = -1

    rows = list(df.itertuples())

    for i, row in enumerate(rows):
        if not in_trade:
            if i <= cooldown_until:
                continue
            signal = signal_fn(row)
            # dual_track returns "track1"/"track2"/None; others return bool
            if signal is None or signal is False:
                signal = False
            if signal:
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

                # Gate B: R:R pre-screen (track-aware for v6)
                if entry_price > stop_loss:
                    risk_pct_entry = (entry_price - stop_loss) / entry_price
                    _is_dual = entry_mode in ("dual_track", "dual_track_v6")
                    _track = signal if _is_dual else "track1"
                    if _track == "track1" and max_stop_t1 is not None:
                        # v6 Track1: allow if stop distance ≤ max_stop_t1 (e.g. 8%)
                        if risk_pct_entry > max_stop_t1:
                            continue
                    elif rr_min > 0:
                        # Track2 / legacy: require R:R ≥ rr_min
                        rr = trail_trigger / risk_pct_entry if risk_pct_entry > 0 else 0.0
                        if rr < rr_min:
                            continue

                # Gate A: SPY trend — block Track1 when SPY < MA20
                if spy_gate and spy_df is not None:
                    try:
                        spy_row = spy_df.asof(row.Index)
                        spy_bear = (not pd.isna(spy_row["ma20"]) and
                                    float(spy_row["Close"]) < float(spy_row["ma20"]))
                    except Exception:
                        spy_bear = False
                    track = signal if entry_mode == "dual_track" else "track1"
                    if spy_bear and track != "track2":
                        continue

                target = entry_price * (1 + target_pct)
                entry_date = next_row.Index
                entry_idx = i + 1
                high_water = entry_price
                trailing_active = False
                # Per-trade trail trigger for dual_track mode
                if entry_mode == "dual_track":
                    active_trail_trigger = TRAIL_TRIGGER_T2 if signal == "track2" else TRAIL_TRIGGER_T1
                else:
                    active_trail_trigger = trail_trigger
                in_trade = True
        else:
            days_held = i - entry_idx
            low = float(row.Low)
            high = float(row.High)
            close = float(row.Close)

            exit_price = None
            exit_reason = None

            if exit_mode == "trailing":
                # Update high water mark
                if high > high_water:
                    high_water = high
                # Activate trailing once trail_trigger gain is reached
                if not trailing_active and high >= entry_price * (1 + active_trail_trigger):
                    trailing_active = True
                # Exit conditions
                if low <= stop_loss:
                    exit_price = stop_loss * (1 - slippage_pct)
                    exit_reason = "stop_loss"
                elif trailing_active and low <= high_water * (1 - trail_pct):
                    exit_price = high_water * (1 - trail_pct) * (1 - slippage_pct)
                    exit_reason = "trail_stop"
                elif days_held >= hold_days * 2:  # double time limit for trailing
                    exit_price = close * (1 - slippage_pct)
                    exit_reason = "time_exit"
            else:
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
                cooldown_until = i + 10  # 10-day cooldown before re-entry

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
    """Download price data for symbols + SPY. Returns (raw, get_df, spy_return).
    period can be yfinance periods ("6mo","1y","2y") or a calendar year ("2024","2023").
    """
    all_syms = list(set(symbols + ["SPY"]))
    # Support calendar year periods
    year_map = {str(y): (f"{y}-01-01", f"{y}-12-31") for y in range(2020, 2030)}
    if period in year_map:
        start, end = year_map[period]
        raw = yf.download(
            all_syms,
            start=start,
            end=end,
            group_by="ticker",
            auto_adjust=True,
            threads=True,
            progress=False,
        )
    else:
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


def _build_spy_ma(get_df_fn) -> "pd.DataFrame | None":
    """Return SPY Close + MA20 DataFrame indexed by date, for Gate A checks."""
    try:
        spy_df = get_df_fn("SPY")
        if spy_df.empty:
            return None
        spy_df = spy_df[["Close"]].copy()
        spy_df["ma20"] = spy_df["Close"].rolling(20).mean()
        return spy_df
    except Exception:
        return None


def run_backtest(
    symbols: list[str],
    period: str = "2y",
    hold_days: int = 10,
    target_pct: float = 0.08,
    stop_type: str = "atr",
    entry_mode: str = "normal",
    exit_mode: str = "fixed",
    trail_pct: float = 0.08,
    trail_trigger: float = 0.08,
    trail_trigger_t1: float | None = None,
    trail_trigger_t2: float | None = None,
    spy_gate: bool = False,
    rr_min: float = 0.0,
    max_stop_t1: float | None = None,
) -> dict:
    """
    Run walk-forward backtest on given symbols.
    stop_type: "atr" | "fixed_3pct" | "fixed_5pct"
    entry_mode: "normal" | "strict" | "dual_track"
    exit_mode: "fixed" | "trailing"
    Returns stats dict with equity_curve and trades list.
    """
    print(f"[backtest] {len(symbols)} symbols, period={period}, entry={entry_mode}, exit={exit_mode}…")
    try:
        _get_df, spy_return = _download_data(symbols, period)
    except Exception as e:
        return {"error": str(e)}

    spy_df_ma = _build_spy_ma(_get_df) if spy_gate else None

    all_trades: list[dict] = []
    for sym in symbols:
        try:
            df = _get_df(sym)
            if df.empty or len(df) < 60:
                continue
            trades = _simulate_symbol(sym, df, hold_days, target_pct,
                                      stop_type=stop_type, entry_mode=entry_mode,
                                      exit_mode=exit_mode, trail_pct=trail_pct,
                                      trail_trigger=trail_trigger,
                                      trail_trigger_t1=trail_trigger_t1,
                                      trail_trigger_t2=trail_trigger_t2,
                                      spy_gate=spy_gate, rr_min=rr_min,
                                      max_stop_t1=max_stop_t1,
                                      spy_df=spy_df_ma)
            all_trades.extend(trades)
        except Exception as e:
            print(f"[backtest] {sym} error: {e}")

    return _compute_stats(all_trades, spy_return)


def backtest_compare_versions(
    symbols: list[str],
    period: str = "1y",
    hold_days: int = 10,
) -> dict:
    """
    Run v_prev vs v_current backtest on the same symbols and time window.
    Reads version definitions from data/versions.json (latest two entries).
    Returns side-by-side comparison dict.
    """
    import json
    from pathlib import Path

    versions_path = Path(__file__).parent.parent.parent / "data" / "versions.json"
    try:
        versions = json.loads(versions_path.read_text())
    except Exception as e:
        return {"error": f"Cannot read versions.json: {e}"}

    if len(versions) < 2:
        return {"error": "Need at least 2 versions in versions.json"}

    v_prev    = versions[-2]
    v_current = versions[-1]

    print(f"[backtest] Comparing {v_prev['version']} vs {v_current['version']} — {len(symbols)} symbols, period={period}")
    try:
        _get_df, spy_return = _download_data(symbols, period)
    except Exception as e:
        return {"error": str(e)}

    # Precompute SPY MA20 once — reused for any version with spy_gate=true
    _spy_df_ma = _build_spy_ma(_get_df)

    results = {}
    for v in [v_prev, v_current]:
        p = v["backtest_params"]
        spy_gate    = bool(p.get("spy_gate", False))
        rr_min      = float(p.get("rr_min", 0.0))
        max_stop_t1 = p.get("max_stop_t1")
        if max_stop_t1 is not None:
            max_stop_t1 = float(max_stop_t1)
        spy_df   = _spy_df_ma if spy_gate else None
        all_trades: list[dict] = []
        for sym in symbols:
            try:
                df = _get_df(sym)
                if df.empty or len(df) < 60:
                    continue
                trades = _simulate_symbol(
                    sym, df, hold_days,
                    target_pct=p.get("target_pct", 0.08),
                    stop_type=p.get("stop_type", "atr"),
                    entry_mode=p.get("entry_mode", "strict"),
                    exit_mode=p.get("exit_mode", "trailing"),
                    trail_pct=p.get("trail_pct", 0.08),
                    trail_trigger=p.get("trail_trigger", 0.12),
                    trail_trigger_t1=p.get("trail_trigger_t1"),
                    trail_trigger_t2=p.get("trail_trigger_t2"),
                    spy_gate=spy_gate, rr_min=rr_min,
                    max_stop_t1=max_stop_t1, spy_df=spy_df,
                )
                all_trades.extend(trades)
            except Exception as e:
                print(f"[backtest] {sym} ({v['version']}) error: {e}")

        stats = _compute_stats(all_trades, spy_return)
        results[v["version"]] = {
            "label":       v["label"],
            "description": v["description"],
            "created_at":  v["created_at"],
            "stats":       stats,
        }
        print(f"[backtest] {v['version']}: trades={stats.get('total_trades')}, "
              f"win={stats.get('win_rate')}%, ret={stats.get('total_return_pct')}%")

    return {
        "status":       "done",
        "period":       period,
        "symbols_count": len(symbols),
        "spy_return_pct": spy_return,
        "v_prev":       results[v_prev["version"]],
        "v_current":    results[v_current["version"]],
    }


def compare_exit_strategies(
    symbols: list[str],
    period: str = "1y",
    hold_days: int = 10,
    target_pct: float = 0.12,
    trail_pct: float = 0.08,
    stop_type: str = "atr",
) -> dict:
    """
    A vs B exit strategy comparison:
    A: fixed target (sell immediately when target_pct hit)
    B: trailing stop (let winner run, sell on trail_pct pullback from high)
    Same entry signals, same stop-loss, only exit differs.
    """
    print(f"[backtest] A/B exit comparison — {len(symbols)} symbols, period={period}…")
    try:
        _get_df, spy_return = _download_data(symbols, period)
    except Exception as e:
        return {"error": str(e)}

    trades_a, trades_b = [], []
    for sym in symbols:
        try:
            df = _get_df(sym)
            if df.empty or len(df) < 60:
                continue
            trades_a.extend(_simulate_symbol(sym, df, hold_days, target_pct,
                                             stop_type=stop_type, exit_mode="fixed"))
            trades_b.extend(_simulate_symbol(sym, df, hold_days, target_pct,
                                             stop_type=stop_type, exit_mode="trailing",
                                             trail_pct=trail_pct))
        except Exception as e:
            print(f"[backtest] {sym} error: {e}")

    stats_a = _compute_stats(trades_a, spy_return)
    stats_b = _compute_stats(trades_b, spy_return)
    print(f"[backtest] A: {len(trades_a)} trades | B: {len(trades_b)} trades")
    return {"A_fixed": stats_a, "B_trailing": stats_b, "spy_return_pct": spy_return}


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
