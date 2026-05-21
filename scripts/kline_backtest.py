"""
K-Line Pattern Backtest
=======================
Validates whether adding candlestick pattern filtering (candle_quality score)
improves swing-trade performance versus using RSI + MA20 alone.

Methodology
-----------
1. Walk-forward on ~60 liquid stocks, 6-month lookback.
2. Each trading day: compute RSI, MA20, K-line patterns using only prior data.
3. Entry condition (mirrors live scanner):
     - RSI between 35–65
     - Price within 8% of MA20
     - Volume ratio ≥ 1.0x (some activity)
4. Simulate trade: enter at next-day open, hold ≤ 10 days.
   Exit on 5% stop-loss OR 8% profit target OR time exit.
5. Compare results sliced by candle_quality:
     +2 (strong bullish), +1 (mild bullish), 0 (neutral), -1/-2 (bearish)
6. Key question: do +1/+2 candle trades outperform 0/-1/-2?

Usage:
    cd /Users/tingaling97/personal-trading-agent
    python scripts/kline_backtest.py [--months 6] [--stop 5] [--target 8]
"""
from __future__ import annotations

import argparse
import os
import sys
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import yfinance as yf

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.monitor.sp500_scanner import compute_kline_patterns


# ── Test universe (diverse, liquid) ─────────────────────────────────────────
UNIVERSE = [
    # Mega-cap tech
    "AAPL", "MSFT", "NVDA", "AMZN", "META", "GOOGL", "TSLA", "AVGO",
    # Mid-cap tech / AI
    "AMD", "QCOM", "ADBE", "CRM", "INTU", "SNOW", "DDOG", "CRWD", "PANW",
    # Financials
    "JPM", "GS", "V", "MA", "BAC", "AXP",
    # Consumer
    "COST", "MCD", "NKE", "SBUX", "HD", "LOW",
    # Healthcare
    "UNH", "LLY", "JNJ", "ABBV", "MRK", "TMO",
    # Energy
    "XOM", "CVX",
    # Industrials
    "CAT", "HON", "RTX", "UPS",
    # Growth mid-caps
    "APP", "ARM", "MSTR", "HOOD", "AFRM", "UPST", "SOFI",
    "MOD", "KTOS", "RKLB", "IONQ",
]


# ── Helpers ──────────────────────────────────────────────────────────────────

def _scalar(val):
    if isinstance(val, pd.Series):
        return float(val.iloc[0])
    return float(val)


def _compute_rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain  = delta.clip(lower=0).rolling(period).mean()
    loss  = (-delta.clip(upper=0)).rolling(period).mean()
    rs    = gain / loss.replace(0, np.nan)
    return 100 - 100 / (1 + rs)


def _fetch(sym: str, start: str, end: str) -> pd.DataFrame | None:
    try:
        df = yf.download(sym, start=start, end=end, auto_adjust=True,
                         progress=False)
        # Flatten multi-level columns (Price, Ticker) → single level (Price)
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        # Some tickers may still have duplicate column names; keep first occurrence
        df = df.loc[:, ~df.columns.duplicated()]
        # Ensure every OHLCV column is truly a 1-D Series (squeeze if needed)
        for col in ("Open", "High", "Low", "Close", "Volume"):
            if col in df.columns and isinstance(df[col], pd.DataFrame):
                df[col] = df[col].iloc[:, 0]
        if df.empty or len(df) < 30:
            return None
        return df
    except Exception:
        return None


def _compute_signals_on_day(df_up_to: pd.DataFrame) -> dict | None:
    """
    Given OHLCV data up to (and including) today, compute entry signals.
    Returns None if data insufficient or entry conditions not met.
    """
    if len(df_up_to) < 25:
        return None

    closes  = df_up_to["Close"].dropna()
    volumes = df_up_to["Volume"].dropna()

    # Squeeze any 1-column DataFrames to Series
    if isinstance(closes, pd.DataFrame):
        closes = closes.iloc[:, 0]
    if isinstance(volumes, pd.DataFrame):
        volumes = volumes.iloc[:, 0]

    price = _scalar(closes.iloc[-1])

    # RSI
    rsi_ser = _compute_rsi(closes)
    rsi = _scalar(rsi_ser.iloc[-1])
    if np.isnan(rsi):
        return None

    # MA20
    ma20 = _scalar(closes.rolling(20).mean().iloc[-1])
    if np.isnan(ma20) or ma20 == 0:
        return None
    vs_ma20 = (price - ma20) / ma20 * 100

    # Volume ratio (yesterday vs 20-day avg)
    vol_prev = _scalar(volumes.iloc[-2]) if len(volumes) >= 2 else _scalar(volumes.iloc[-1])
    vol_avg_val = volumes.iloc[-22:-2].mean() if len(volumes) >= 22 else volumes.iloc[:-1].mean()
    vol_avg  = _scalar(vol_avg_val) if not isinstance(vol_avg_val, float) else vol_avg_val
    vol_ratio = vol_prev / vol_avg if vol_avg > 0 else 1.0

    # ── Entry filter (mirrors live scanner) ──────────────────────────────────
    if not (35 <= rsi <= 65):
        return None
    if vs_ma20 < -10 or vs_ma20 > 8:   # not too far below or above MA20
        return None
    if vol_ratio < 0.5:                  # ignore extremely thin days
        return None

    # ── K-line patterns（必须在 today_bull 过滤前计算）────────────────────────
    kline = compute_kline_patterns(df_up_to)

    # 右侧交易：今日必须收阳（Layer 1 硬性条件）
    if not kline.get("today_bull", False):
        return None

    return {
        "rsi":            round(rsi, 1),
        "vs_ma20":        round(vs_ma20, 1),
        "vol_ratio":      round(vol_ratio, 2),
        "candle_quality": kline.get("candle_quality", 0),
        "candle_desc":    kline.get("candle_desc", ""),
        "patterns":       kline.get("patterns", []),
        "price":          round(price, 2),
    }


def _simulate(df_full: pd.DataFrame, entry_idx: int,
              stop_pct: float, target_pct: float, hold_days: int) -> dict:
    """
    Enter at open of (entry_idx + 1), hold up to hold_days.
    Returns pnl_pct, exit_reason, days_held.
    """
    if entry_idx + 1 >= len(df_full):
        return {"pnl_pct": None, "exit_reason": "no_data", "days_held": 0}

    entry_open = _scalar(df_full["Open"].iloc[entry_idx + 1])
    slippage   = 0.002   # 0.2% slippage
    ep         = entry_open * (1 + slippage)
    stop_lvl   = ep * (1 - stop_pct / 100)
    tgt_lvl    = ep * (1 + target_pct / 100)

    future = df_full.iloc[entry_idx + 1 : entry_idx + 1 + hold_days]
    for i, (_, row) in enumerate(future.iterrows()):
        lo = _scalar(row["Low"])
        hi = _scalar(row["High"])
        if lo <= stop_lvl:
            exit_p = stop_lvl * (1 - slippage)
            return {"pnl_pct": round((exit_p - ep) / ep * 100, 2),
                    "exit_reason": "stop", "days_held": i + 1}
        if hi >= tgt_lvl:
            exit_p = tgt_lvl * (1 - slippage)
            return {"pnl_pct": round((exit_p - ep) / ep * 100, 2),
                    "exit_reason": "target", "days_held": i + 1}

    if len(future) == 0:
        return {"pnl_pct": None, "exit_reason": "no_data", "days_held": 0}

    last_close = _scalar(future.iloc[-1]["Close"])
    exit_p     = last_close * (1 - slippage)
    return {"pnl_pct": round((exit_p - ep) / ep * 100, 2),
            "exit_reason": "time", "days_held": len(future)}


def _backtest_symbol(sym: str, df: pd.DataFrame,
                     stop_pct: float, target_pct: float,
                     hold_days: int) -> list[dict]:
    """Walk-forward through all trading days for one symbol."""
    trades = []
    last_entry_idx = -999   # enforce min 5-day gap between entries on same symbol

    for i in range(30, len(df) - 1):   # need 30 bars history; need 1 bar future
        if i - last_entry_idx < 5:
            continue

        df_window = df.iloc[:i + 1]    # data up to and including today
        sig = _compute_signals_on_day(df_window)
        if sig is None:
            continue

        sim = _simulate(df, i, stop_pct, target_pct, hold_days)
        if sim["pnl_pct"] is None:
            continue

        trades.append({
            "symbol":         sym,
            "date":           str(df.index[i].date()),
            "candle_quality": sig["candle_quality"],
            "candle_desc":    sig["candle_desc"],
            "patterns":       sig["patterns"],
            "rsi":            sig["rsi"],
            "vs_ma20":        sig["vs_ma20"],
            "vol_ratio":      sig["vol_ratio"],
            "entry_price":    sig["price"],
            "pnl_pct":        sim["pnl_pct"],
            "exit_reason":    sim["exit_reason"],
            "days_held":      sim["days_held"],
        })
        last_entry_idx = i

    return trades


# ── Stats helpers ─────────────────────────────────────────────────────────────

def _stats(trades: list[dict], label: str) -> dict:
    pnls   = [t["pnl_pct"] for t in trades if t["pnl_pct"] is not None]
    if not pnls:
        print(f"  {label}: 0 trades")
        return {}
    wins   = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    wr     = len(wins) / len(pnls) * 100
    avg_w  = np.mean(wins)   if wins   else 0.0
    avg_l  = np.mean(losses) if losses else 0.0
    pf     = abs(sum(wins) / sum(losses)) if losses and sum(losses) != 0 else float("inf")
    total  = sum(pnls)
    exp    = total / len(pnls)   # expectancy per trade

    # Wilson CI for win rate (90%)
    n  = len(pnls)
    p  = len(wins) / n
    z  = 1.645
    lo = (p + z*z/(2*n) - z*((p*(1-p)/n + z*z/(4*n*n))**0.5)) / (1 + z*z/n)
    hi = (p + z*z/(2*n) + z*((p*(1-p)/n + z*z/(4*n*n))**0.5)) / (1 + z*z/n)

    print(f"  {label}")
    print(f"    Trades      : {len(pnls):>4}  (wins={len(wins)}, losses={len(losses)})")
    print(f"    Win rate    : {wr:5.1f}%  [90% CI {lo*100:.1f}%–{hi*100:.1f}%]")
    print(f"    Avg win/loss: {avg_w:+.2f}% / {avg_l:+.2f}%")
    print(f"    Profit fac  : {pf:.2f}")
    print(f"    Expectancy  : {exp:+.2f}% per trade")
    print(f"    Total P&L   : {total:+.2f}%")
    print()
    return {"n": n, "wr": wr, "pf": pf, "exp": exp, "total": total}


def _exit_breakdown(trades: list[dict]):
    counts = defaultdict(int)
    for t in trades:
        counts[t["exit_reason"]] += 1
    total = len(trades)
    parts = [f"{k}={v}({v/total*100:.0f}%)" for k, v in sorted(counts.items())]
    print(f"    Exit mix: {', '.join(parts)}")


# ── Main ──────────────────────────────────────────────────────────────────────

def main(months: int = 6, stop_pct: float = 5.0,
         target_pct: float = 8.0, hold_days: int = 10):
    end_dt   = datetime.now()
    start_dt = end_dt - timedelta(days=months * 31 + 30)   # +30 for indicator warm-up
    start_s  = start_dt.strftime("%Y-%m-%d")
    end_s    = end_dt.strftime("%Y-%m-%d")

    print(f"\n{'='*70}")
    print(f"K-LINE BACKTEST  |  {start_s} → {end_s}  |  {len(UNIVERSE)} symbols")
    print(f"Params: stop={stop_pct}%  target={target_pct}%  hold_days={hold_days}")
    print(f"{'='*70}\n")

    # Download data in parallel
    print("Downloading price data (sequential)...")
    dfs: dict[str, pd.DataFrame] = {}
    for sym in UNIVERSE:
        df = _fetch(sym, start_s, end_s)
        if df is not None:
            dfs[sym] = df
            print(f"  {sym} OK ({len(df)} bars)", end="\r")
    print(f"  Downloaded {len(dfs)}/{len(UNIVERSE)} symbols        \n")

    # Walk-forward backtest
    print("Running walk-forward simulation...")
    all_trades: list[dict] = []
    for sym, df in dfs.items():
        trades = _backtest_symbol(sym, df, stop_pct, target_pct, hold_days)
        all_trades.extend(trades)
        if trades:
            print(f"  {sym:<8} {len(trades):>3} signals", end="\r")

    print(f"\n  Total signals generated: {len(all_trades)}\n")

    if not all_trades:
        print("No trades generated.")
        return

    # ── Overall stats ──────────────────────────────────────────────────────────
    print("=" * 70)
    print("OVERALL (all entry conditions, no K-line filter)")
    print("=" * 70)
    _stats(all_trades, "All trades")
    _exit_breakdown(all_trades)

    # ── Slice by candle_quality ────────────────────────────────────────────────
    print("\n" + "=" * 70)
    print("BY CANDLE QUALITY SCORE")
    print("=" * 70)

    buckets = {
        "🕯️+2  Strong bullish (hammer/engulf/pullback_bull/strong_bull)":
            [t for t in all_trades if t["candle_quality"] == 2],
        "🕯️+1  Mild bullish":
            [t for t in all_trades if t["candle_quality"] == 1],
        "🕯️ 0  Neutral":
            [t for t in all_trades if t["candle_quality"] == 0],
        "🕯️-1  Mild bearish":
            [t for t in all_trades if t["candle_quality"] == -1],
        "🕯️-2  Strong bearish (bearish_engulf/strong_bear/vol_expand_down)":
            [t for t in all_trades if t["candle_quality"] == -2],
    }

    results = {}
    for label, trades in buckets.items():
        if trades:
            results[label] = _stats(trades, label)
            _exit_breakdown(trades)

    # ── Filtered strategy: ONLY trade quality ≥ +1 ────────────────────────────
    print("\n" + "=" * 70)
    print("FILTERED: only enter on candle_quality ≥ +1")
    print("(equivalent to live scanner STRONG_BUY / BUY signal)")
    print("=" * 70)
    pos_trades = [t for t in all_trades if t["candle_quality"] >= 1]
    neg_trades = [t for t in all_trades if t["candle_quality"] < 1]
    s_pos = _stats(pos_trades, "Quality ≥ +1  (ENTER)")
    _exit_breakdown(pos_trades)
    s_neg = _stats(neg_trades, "Quality  < +1  (SKIP in new strategy)")
    _exit_breakdown(neg_trades)

    # ── Pattern breakdown ──────────────────────────────────────────────────────
    print("\n" + "=" * 70)
    print("TOP PATTERNS — win rate and expectancy")
    print("=" * 70)
    pattern_stats: dict[str, list] = defaultdict(list)
    for t in all_trades:
        for pat in t.get("patterns", []):
            if t["pnl_pct"] is not None:
                pattern_stats[pat].append(t["pnl_pct"])

    rows = []
    for pat, pnls in pattern_stats.items():
        n    = len(pnls)
        wr   = sum(1 for p in pnls if p > 0) / n * 100
        pf_w = [p for p in pnls if p > 0]
        pf_l = [p for p in pnls if p <= 0]
        pf   = abs(sum(pf_w)/sum(pf_l)) if pf_l and sum(pf_l) != 0 else float("inf")
        exp  = np.mean(pnls)
        rows.append((pat, n, wr, pf, exp))

    rows.sort(key=lambda x: x[4], reverse=True)   # sort by expectancy
    print(f"  {'Pattern':<25} {'N':>5} {'WR%':>6} {'PF':>6} {'Exp%':>7}")
    print(f"  {'-'*25} {'-'*5} {'-'*6} {'-'*6} {'-'*7}")
    for pat, n, wr, pf, exp in rows:
        pf_s = f"{pf:.2f}" if pf < 99 else "  inf"
        print(f"  {pat:<25} {n:>5} {wr:>6.1f} {pf_s:>6} {exp:>+7.2f}%")

    # ── RSI bucket analysis ────────────────────────────────────────────────────
    print("\n" + "=" * 70)
    print("RSI BUCKET ANALYSIS (with K-line ≥ +1 filter applied)")
    print("=" * 70)
    rsi_buckets = [
        ("RSI 35-45 (recovery)", 35, 45),
        ("RSI 45-55 (sweet spot)", 45, 55),
        ("RSI 55-60 (extended)",  55, 60),
        ("RSI 60-65 (hot)",       60, 65),
    ]
    for label, lo, hi in rsi_buckets:
        subset = [t for t in pos_trades if lo <= t["rsi"] < hi]
        if subset:
            pnls = [t["pnl_pct"] for t in subset]
            wr   = sum(1 for p in pnls if p > 0) / len(pnls) * 100
            exp  = np.mean(pnls)
            print(f"  {label:<30} n={len(pnls):>3}  WR={wr:.1f}%  Exp={exp:+.2f}%")

    # ── Key verdict ────────────────────────────────────────────────────────────
    print("\n" + "=" * 70)
    print("VERDICT")
    print("=" * 70)
    if s_pos and s_neg:
        wr_delta  = s_pos.get("wr", 0) - s_neg.get("wr", 0)
        exp_delta = s_pos.get("exp", 0) - s_neg.get("exp", 0)
        pf_pos    = s_pos.get("pf", 1.0)
        pf_neg    = s_neg.get("pf", 1.0)
        skipped_pct = len(neg_trades) / len(all_trades) * 100 if all_trades else 0

        print(f"  K-line filter (quality ≥ +1) vs. no filter:")
        print(f"    Win rate delta  : {wr_delta:+.1f}%")
        print(f"    Expectancy delta: {exp_delta:+.2f}% per trade")
        print(f"    Profit factor   : {pf_pos:.2f} (filtered) vs {pf_neg:.2f} (skipped)")
        print(f"    Trades skipped  : {len(neg_trades)} ({skipped_pct:.0f}% of all signals)")
        print()
        if exp_delta > 0.3 and wr_delta > 3:
            print("  ✅ K-line filter IMPROVES performance. Continue using it.")
        elif exp_delta > 0 and wr_delta > 0:
            print("  ✅ K-line filter shows positive edge. Marginal but consistent.")
        elif exp_delta < -0.3:
            print("  ⚠️  K-line filter may be HURTING performance. Review thresholds.")
        else:
            print("  ➡️  K-line filter effect is neutral. More data needed.")
    print()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--months",  type=int,   default=6,   help="Lookback months (default 6)")
    parser.add_argument("--stop",    type=float, default=5.0, help="Stop loss %% (default 5)")
    parser.add_argument("--target",  type=float, default=8.0, help="Profit target %% (default 8)")
    parser.add_argument("--hold",    type=int,   default=10,  help="Max hold days (default 10)")
    args = parser.parse_args()
    main(months=args.months, stop_pct=args.stop,
         target_pct=args.target, hold_days=args.hold)
