"""
PEAD (Post-Earnings Announcement Drift) Backtest

Tests whether buying 1-2 days after a strong earnings beat yields
better win rates than the current technical momentum strategy.

Entry criteria:
  - EPS surprise >= threshold (default 5%)
  - Price next day is above pre-earnings close (gap held)
  - Volume on earnings day >= 1.5x average
  - RSI < 72 (not overbought)

Exit:
  - Stop: close below pre-earnings close (gap fill = thesis dead)
  - Trailing: +10% activates, 5% pullback triggers
  - Time: hold_days max
"""
from __future__ import annotations
import numpy as np
import pandas as pd
import yfinance as yf
from datetime import timedelta


# ── Config ────────────────────────────────────────────────────────────────────

EPS_SURPRISE_MIN  = 5.0    # % EPS beat required
VOLUME_SURGE_MIN  = 1.5    # earnings-day volume vs 20-day avg
RSI_MAX           = 72     # not overbought at entry
HOLD_DAYS         = 10     # max holding period
TRAIL_TRIGGER     = 0.10   # activate trailing after +10%
TRAIL_PCT         = 0.05   # exit if drops 5% from high water
SLIPPAGE          = 0.003  # 0.3% slippage


# ── Helpers ───────────────────────────────────────────────────────────────────

def _rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain  = delta.clip(lower=0).rolling(period).mean()
    loss  = (-delta.clip(upper=0)).rolling(period).mean()
    rs    = gain / loss.replace(0, np.nan)
    return 100 - 100 / (1 + rs)


def _vol_ratio(volume: pd.Series, window: int = 20) -> pd.Series:
    return volume / volume.rolling(window).mean()


# ── Per-symbol PEAD simulation ────────────────────────────────────────────────

def _simulate_pead(symbol: str, price_df: pd.DataFrame,
                   earnings_df: pd.DataFrame) -> list[dict]:
    """Walk through each qualifying earnings event and simulate the trade."""
    if price_df.empty or earnings_df.empty:
        return []

    df = price_df.copy()
    df["rsi"]       = _rsi(df["Close"])
    df["vol_ratio"] = _vol_ratio(df["Volume"])
    df = df.dropna(subset=["rsi", "vol_ratio"])

    trades = []
    used_dates: set = set()   # prevent overlapping trades

    for earn_dt, row in earnings_df.iterrows():
        surprise = row.get("Surprise(%)")
        if pd.isna(surprise) or surprise < EPS_SURPRISE_MIN:
            continue

        # Normalise earnings timestamp to date
        earn_date = pd.Timestamp(earn_dt).tz_localize(None).normalize()

        # Find the earnings day in price data (or next trading day)
        candidates = df.index[df.index >= earn_date]
        if len(candidates) == 0:
            continue
        earn_idx = df.index.get_loc(candidates[0])

        # Entry = next trading day after earnings
        entry_idx = earn_idx + 1
        if entry_idx >= len(df):
            continue

        entry_date = df.index[entry_idx]

        # Skip if overlapping with a previous trade
        if any(abs((entry_date - d).days) < HOLD_DAYS for d in used_dates):
            continue

        # Pre-earnings reference price (close before earnings day)
        if earn_idx == 0:
            continue
        pre_earn_close = float(df["Close"].iloc[earn_idx - 1])

        # Earnings-day volume surge check
        earn_vol_ratio = float(df["vol_ratio"].iloc[earn_idx])
        if earn_vol_ratio < VOLUME_SURGE_MIN:
            continue

        # Entry price = next-day open + slippage
        entry_price = float(df["Open"].iloc[entry_idx]) * (1 + SLIPPAGE)

        # Gap-held check: entry price must be above pre-earnings close
        if entry_price <= pre_earn_close:
            continue

        # RSI not overbought at entry
        rsi_at_entry = float(df["rsi"].iloc[entry_idx])
        if rsi_at_entry > RSI_MAX:
            continue

        # Stop = pre-earnings close (gap fill = thesis invalid)
        stop = pre_earn_close * (1 - SLIPPAGE)
        if stop >= entry_price:
            continue

        # ── Simulate hold ──────────────────────────────────────────────────
        high_water      = entry_price
        trailing_active = False
        exit_price      = None
        exit_reason     = None
        days_held       = 0

        for j in range(entry_idx + 1, min(entry_idx + 1 + HOLD_DAYS * 2, len(df))):
            r          = df.iloc[j]
            days_held  = j - entry_idx
            low, high, close = float(r["Low"]), float(r["High"]), float(r["Close"])

            if high > high_water:
                high_water = high
            if not trailing_active and high >= entry_price * (1 + TRAIL_TRIGGER):
                trailing_active = True

            if low <= stop:
                exit_price  = stop * (1 - SLIPPAGE)
                exit_reason = "gap_fill_stop"
                break
            if trailing_active and low <= high_water * (1 - TRAIL_PCT):
                exit_price  = high_water * (1 - TRAIL_PCT) * (1 - SLIPPAGE)
                exit_reason = "trail_stop"
                break
            if days_held >= HOLD_DAYS:
                exit_price  = close * (1 - SLIPPAGE)
                exit_reason = "time_exit"
                break

        if exit_price is None:
            continue

        pnl_pct = (exit_price - entry_price) / entry_price * 100
        trades.append({
            "symbol":        symbol,
            "earn_date":     str(earn_date.date()),
            "entry_date":    str(entry_date.date()),
            "entry_price":   round(entry_price, 2),
            "exit_price":    round(exit_price, 2),
            "pnl_pct":       round(pnl_pct, 2),
            "exit_reason":   exit_reason,
            "days_held":     days_held,
            "eps_surprise":  round(float(surprise), 1),
            "vol_surge":     round(earn_vol_ratio, 2),
            "rsi_at_entry":  round(rsi_at_entry, 1),
            "pre_earn_close": round(pre_earn_close, 2),
        })
        used_dates.add(entry_date)

    return trades


# ── Public entry point ────────────────────────────────────────────────────────

def run_pead_backtest(
    symbols: list[str],
    period: str = "2y",
    eps_surprise_min: float = EPS_SURPRISE_MIN,
) -> dict:
    """
    Run PEAD backtest across symbols.
    Returns stats dict comparable to backtester.py output.
    """
    print(f"[pead] Testing {len(symbols)} symbols, period={period}, "
          f"EPS surprise >= {eps_surprise_min}%")

    # Download price data for all symbols at once
    year_map = {str(y): (f"{y}-01-01", f"{y}-12-31") for y in range(2020, 2030)}
    all_syms = list(set(symbols))
    if period in year_map:
        start, end = year_map[period]
        raw = yf.download(all_syms, start=start, end=end,
                          group_by="ticker", auto_adjust=True,
                          threads=True, progress=False)
    else:
        raw = yf.download(all_syms, period=period,
                          group_by="ticker", auto_adjust=True,
                          threads=True, progress=False)

    def _get_df(sym):
        if len(all_syms) == 1:
            return raw
        lvl0 = raw.columns.get_level_values(0)
        return raw[sym] if sym in lvl0 else pd.DataFrame()

    all_trades: list[dict] = []
    skipped = 0

    for sym in symbols:
        try:
            price_df = _get_df(sym)
            if price_df.empty or len(price_df) < 60:
                skipped += 1
                continue

            # Fetch earnings dates (includes EPS surprise %)
            earnings_df = yf.Ticker(sym).earnings_dates
            if earnings_df is None or earnings_df.empty:
                skipped += 1
                continue

            # Filter to period covered by price data
            price_start = price_df.index[0].tz_localize(None)
            price_end   = price_df.index[-1].tz_localize(None)
            earnings_df.index = earnings_df.index.tz_localize(None)
            earnings_df = earnings_df[
                (earnings_df.index >= price_start) &
                (earnings_df.index <= price_end)
            ]

            trades = _simulate_pead(sym, price_df, earnings_df)
            all_trades.extend(trades)
            if trades:
                print(f"[pead] {sym}: {len(trades)} trades")

        except Exception as e:
            print(f"[pead] {sym} error: {e}")
            skipped += 1

    if not all_trades:
        return {"error": "no_trades", "skipped": skipped}

    # ── Stats ─────────────────────────────────────────────────────────────────
    pnls    = [t["pnl_pct"] for t in all_trades]
    winners = [p for p in pnls if p > 0]
    losers  = [p for p in pnls if p <= 0]

    win_rate      = len(winners) / len(pnls) * 100
    avg_win       = float(np.mean(winners)) if winners else 0.0
    avg_loss      = float(np.mean(losers))  if losers  else 0.0
    profit_factor = abs(sum(winners) / sum(losers)) if sum(losers) != 0 else float("inf")

    reasons: dict[str, int] = {}
    for t in all_trades:
        r = t["exit_reason"]
        reasons[r] = reasons.get(r, 0) + 1

    by_surprise = {
        "5-10%":  [t for t in all_trades if 5  <= t["eps_surprise"] < 10],
        "10-20%": [t for t in all_trades if 10 <= t["eps_surprise"] < 20],
        "20%+":   [t for t in all_trades if t["eps_surprise"] >= 20],
    }
    surprise_stats = {}
    for band, ts in by_surprise.items():
        if ts:
            ps = [t["pnl_pct"] for t in ts]
            ws = [p for p in ps if p > 0]
            surprise_stats[band] = {
                "count":    len(ts),
                "win_rate": round(len(ws) / len(ps) * 100, 1),
                "avg_pnl":  round(float(np.mean(ps)), 2),
            }

    return {
        "total_trades":    len(all_trades),
        "win_rate":        round(win_rate, 1),
        "avg_win_pct":     round(avg_win, 2),
        "avg_loss_pct":    round(avg_loss, 2),
        "profit_factor":   round(profit_factor, 2),
        "exit_breakdown":  reasons,
        "surprise_bands":  surprise_stats,
        "skipped_symbols": skipped,
        "trades":          sorted(all_trades, key=lambda t: t["entry_date"]),
    }


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import json
    from pathlib import Path

    # Use our watchlist + top scan candidates as test universe
    wl_file = Path(__file__).parent.parent.parent / "data" / "watchlist.json"
    try:
        symbols = json.loads(wl_file.read_text())
    except Exception:
        symbols = []

    # Supplement with a core S&P 500 sample if watchlist is small
    CORE = [
        "AAPL","MSFT","NVDA","AMZN","GOOGL","META","TSLA","AVGO","AMD","QCOM",
        "MRVL","MU","AMAT","SPOT","APP","GS","V","LIN","AMGN","ODFL",
        "DOCU","DHR","RTX","ABT","JNJ","ACN","DLTR","ENPH","VIPS","QS",
    ]
    for s in CORE:
        if s not in symbols:
            symbols.append(s)

    print(f"Testing {len(symbols)} symbols...\n")
    result = run_pead_backtest(symbols, period="2y")

    print("\n" + "="*55)
    print("  PEAD 回测结果")
    print("="*55)
    if "error" in result:
        print(f"  错误: {result['error']}")
    else:
        print(f"  总交易次数: {result['total_trades']}")
        print(f"  胜率:       {result['win_rate']}%")
        print(f"  平均盈利:   +{result['avg_win_pct']}%")
        print(f"  平均亏损:   {result['avg_loss_pct']}%")
        print(f"  盈亏比:     {result['profit_factor']}x")
        print(f"  离场原因:   {result['exit_breakdown']}")
        print(f"\n  按超预期幅度分组:")
        for band, s in result["surprise_stats"].items() if "surprise_stats" in result else result.get("surprise_bands", {}).items():
            print(f"    {band}: {s['count']}笔 | 胜率 {s['win_rate']}% | 均收益 {s['avg_pnl']:+.2f}%")
    print("="*55)
