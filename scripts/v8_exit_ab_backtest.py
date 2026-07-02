#!/usr/bin/env python3
"""v8 exit A/B backtest.

Compares the current v8 exit rules with two profit-protection variants while
keeping the entry logic fixed:

- baseline: -8% hard stop, +6% activate / 8% giveback trail, MA20 below 2d
- A:        -8% hard stop, +5% breakeven, +6% activate / 5% giveback trail
- B:        -8% hard stop, sell half at +6%, remaining +6% activate / 8% trail,
            5-trading-day cooldown after hard-stop exits

This is an analysis script only; it does not place orders or change strategy.
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter
from dataclasses import dataclass
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv()

import numpy as np
import pandas as pd

from src.trader.alpaca_trader import get_client


START = "2022-09-01"
SIM_START = "2023-01-01"
MAX_POS = 10
STOP = 0.08
COST = 0.0005
MOM_DAYS = 60
CACHE_FILE = Path(__file__).resolve().parent.parent / "data" / "v8_exit_ab_ohlc_cache.pkl"


@dataclass
class Variant:
    name: str
    trail_trigger: float = 0.06
    trail_giveback: float = 0.08
    breakeven_trigger: float | None = None
    partial_take_profit: float | None = None
    partial_frac: float = 0.5
    hard_stop_cooldown_days: int = 0


VARIANTS = [
    Variant("baseline"),
    Variant("A_breakeven_trail5", trail_giveback=0.05, breakeven_trigger=0.05),
    Variant("B_half_take_cooldown", partial_take_profit=0.06, hard_stop_cooldown_days=5),
]

GRID_VARIANTS = [
    Variant("baseline"),
    Variant("BE4_trail4", trail_giveback=0.04, breakeven_trigger=0.04),
    Variant("BE5_trail4", trail_giveback=0.04, breakeven_trigger=0.05),
    Variant("BE5_trail5", trail_giveback=0.05, breakeven_trigger=0.05),
    Variant("BE6_trail5", trail_giveback=0.05, breakeven_trigger=0.06),
    Variant("BE6_trail6", trail_giveback=0.06, breakeven_trigger=0.06),
]


def fetch_ohlc(symbols: list[str], start: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    if CACHE_FILE.exists():
        try:
            cached = pd.read_pickle(CACHE_FILE)
            close, high = cached["close"], cached["high"]
            if not close.empty and "SPY" in close.columns:
                print(f"Loaded cached OHLC: {close.shape[1]} symbols x {close.shape[0]} days", flush=True)
                return close, high
        except Exception as e:
            print(f"  [warn] cache read failed: {e}", flush=True)

    client = get_client()
    closes: dict[str, pd.Series] = {}
    highs: dict[str, pd.Series] = {}

    for i in range(0, len(symbols), 50):
        chunk_raw = symbols[i:i + 50]
        print(f"  fetch chunk {i//50 + 1}/{(len(symbols) + 49)//50}: {chunk_raw[0]}...{chunk_raw[-1]}", flush=True)
        try:
            df = client.get_bars(chunk_raw, "1Day", start=start, adjustment="all", feed="iex").df
        except Exception as e:
            print(f"  [warn] chunk {i//50 + 1}: batch failed, trying one-by-one: {e}", flush=True)
            for raw in chunk_raw:
                try:
                    df1 = client.get_bars(raw, "1Day", start=start, adjustment="all", feed="iex").df
                    if df1 is not None and len(df1):
                        closes[raw] = df1["close"]
                        highs[raw] = df1["high"]
                except Exception as e1:
                    print(f"    [skip] {raw}: {e1}", flush=True)
            continue
        if df is None or len(df) == 0:
            continue
        if "symbol" in df.columns:
            for sym, g in df.groupby("symbol"):
                closes[sym] = g["close"]
                highs[sym] = g["high"]
        elif isinstance(df.index, pd.MultiIndex):
            for sym in df.index.get_level_values(0).unique():
                g = df.loc[sym]
                closes[sym] = g["close"]
                highs[sym] = g["high"]
        else:
            raw = chunk_raw[0]
            closes[raw] = df["close"]
            highs[raw] = df["high"]

    close = pd.DataFrame(closes).sort_index()
    high = pd.DataFrame(highs).sort_index()
    close.index = pd.to_datetime(close.index).tz_localize(None)
    high.index = pd.to_datetime(high.index).tz_localize(None)
    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    pd.to_pickle({"close": close, "high": high.reindex(close.index)}, CACHE_FILE)
    return close, high.reindex(close.index)


def rsi(series: pd.Series, n: int = 14) -> pd.Series:
    d = series.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return 100 - 100 / (1 + up / dn.replace(0, 1e-9))


def max_dd(eq: np.ndarray) -> float:
    peak = np.maximum.accumulate(eq)
    return float(((eq - peak) / peak).min()) if len(eq) else 0.0


def cagr(eq: np.ndarray, dates: pd.DatetimeIndex) -> float:
    if len(eq) < 2:
        return 0.0
    years = max((dates[-1] - dates[0]).days / 365.25, 1e-9)
    return float(eq[-1] ** (1 / years) - 1)


def run_variant(
    variant: Variant,
    close: pd.DataFrame,
    high: pd.DataFrame,
    sim_dates: pd.DatetimeIndex,
) -> dict:
    ma20 = close.rolling(20).mean()
    ma50 = close.rolling(50).mean()
    slope = ma50 - ma50.shift(5)
    mom = close / close.shift(MOM_DAYS) - 1
    rsi_p = close.apply(rsi)

    px_a = close.values
    hi_a = high.values
    ma20_a = ma20.values
    ma50_a = ma50.values
    slope_a = slope.values
    mom_a = mom.values
    rsi_a = rsi_p.values
    cols = list(close.columns)

    sim_date_set = set(sim_dates)
    sim_idx = [i for i, d in enumerate(close.index) if d in sim_date_set]
    each = 1.0 / MAX_POS
    cash = 1.0
    positions: dict[int, dict] = {}
    cooldown_until: dict[int, int] = {}
    eq = np.empty(len(sim_idx))
    trades: list[dict] = []
    exit_counts: Counter[str] = Counter()

    for k, i in enumerate(sim_idx):
        row = px_a[i]
        high_row = hi_a[i]

        for ci in list(positions):
            p = row[ci]
            day_high = high_row[ci]
            if np.isnan(p):
                continue
            pos = positions[ci]
            if not np.isnan(day_high):
                pos["hi"] = max(pos["hi"], day_high)
            else:
                pos["hi"] = max(pos["hi"], p)

            entry = pos["entry"]
            gain_close = p / entry - 1
            gain_high = pos["hi"] / entry - 1
            m20 = ma20_a[i, ci]
            below2 = (
                i >= 1
                and not np.isnan(m20)
                and not np.isnan(ma20_a[i - 1, ci])
                and p < m20
                and px_a[i - 1, ci] < ma20_a[i - 1, ci]
            )

            reason = None
            exit_price = p

            if gain_close <= -STOP:
                reason = "hard_stop"
            elif variant.breakeven_trigger is not None and pos.get("breakeven_active") and p <= entry:
                reason = "breakeven"
                exit_price = entry
            elif gain_high >= variant.trail_trigger and p <= pos["hi"] * (1 - variant.trail_giveback):
                reason = "trail"
            elif below2:
                reason = "ma20_2d"

            if (
                variant.breakeven_trigger is not None
                and not pos.get("breakeven_active")
                and gain_high >= variant.breakeven_trigger
            ):
                pos["breakeven_active"] = True

            if (
                variant.partial_take_profit is not None
                and not pos.get("partial_done")
                and gain_high >= variant.partial_take_profit
            ):
                sell_frac = pos["frac"] * variant.partial_frac
                cash += sell_frac * (1 + variant.partial_take_profit) * (1 - COST)
                pos["frac"] -= sell_frac
                pos["partial_done"] = True
                exit_counts["partial_take"] += 1

            if reason:
                cash += pos["frac"] * (exit_price / entry) * (1 - COST)
                ret = exit_price / entry - 1
                trades.append({
                    "symbol": cols[ci],
                    "ret": ret,
                    "days": k - pos["entry_k"] + 1,
                    "reason": reason,
                })
                exit_counts[reason] += 1
                if reason == "hard_stop" and variant.hard_stop_cooldown_days:
                    cooldown_until[ci] = k + variant.hard_stop_cooldown_days
                del positions[ci]

        free = MAX_POS - len(positions)
        if free > 0 and cash > each * 0.5:
            elig = (
                (row > ma50_a[i])
                & (slope_a[i] > 0)
                & (rsi_a[i] >= 50)
                & (rsi_a[i] <= 80)
                & (mom_a[i] > 0)
                & (row <= ma20_a[i] * 1.15)
                & ~np.isnan(row)
            )
            for ci in positions:
                elig[ci] = False
            for ci, until_k in cooldown_until.items():
                if k <= until_k:
                    elig[ci] = False
            idx = np.where(elig)[0]
            if len(idx):
                idx = idx[np.argsort(-mom_a[i][idx])][:free]
                for ci in idx:
                    if cash < each * 0.5:
                        break
                    spend = min(each, cash)
                    positions[ci] = {
                        "entry": row[ci],
                        "frac": spend * (1 - COST),
                        "hi": row[ci],
                        "entry_k": k,
                    }
                    cash -= spend

        val = cash
        for ci, pos in positions.items():
            p = row[ci]
            if not np.isnan(p):
                val += pos["frac"] * (p / pos["entry"])
        eq[k] = val

    trade_rets = np.array([t["ret"] for t in trades], dtype=float)
    trade_days = np.array([t["days"] for t in trades], dtype=float)
    return {
        "name": variant.name,
        "eq": eq,
        "total_return": eq[-1] - 1,
        "cagr": cagr(eq, sim_dates),
        "max_dd": max_dd(eq),
        "trades": len(trades),
        "win_rate": float((trade_rets > 0).mean()) if len(trade_rets) else 0.0,
        "avg_trade": float(trade_rets.mean()) if len(trade_rets) else 0.0,
        "median_trade": float(np.median(trade_rets)) if len(trade_rets) else 0.0,
        "avg_days": float(trade_days.mean()) if len(trade_days) else 0.0,
        "exits": dict(exit_counts),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default=SIM_START)
    parser.add_argument("--grid", action="store_true", help="run breakeven/trailing grid instead of A/B")
    args = parser.parse_args()

    uni = Path(__file__).resolve().parent.parent / "data" / "sp500_constituents.txt"
    symbols = sorted(set(uni.read_text().split()))
    symbols_with_bench = symbols + ["SPY"]

    print(f"Universe: {len(symbols)} symbols | fetch from {START} | simulate from {args.start}", flush=True)
    print("Fetching daily bars from Alpaca IEX...", flush=True)
    close, high = fetch_ohlc(symbols_with_bench, START)
    if close.empty or "SPY" not in close.columns:
        raise SystemExit("No data or missing SPY")

    # Keep the same available-date panel for all variants.
    close = close[close.index <= pd.Timestamp(date.today().isoformat())]
    high = high.reindex_like(close)
    sim_dates = close.index[close.index >= pd.Timestamp(args.start)]
    strategy_close = close.drop(columns=["SPY"], errors="ignore")
    strategy_high = high[strategy_close.columns]

    print(f"Fetched: {strategy_close.shape[1]} symbols x {strategy_close.shape[0]} days", flush=True)
    print(f"Sim dates: {sim_dates[0].date()} ~ {sim_dates[-1].date()} ({len(sim_dates)} bars)\n", flush=True)

    variants = GRID_VARIANTS if args.grid else VARIANTS
    results = [run_variant(v, strategy_close, strategy_high, sim_dates) for v in variants]

    spy = close["SPY"].reindex(sim_dates).dropna()
    spy_eq = (spy / spy.iloc[0]).values
    spy_ret = spy_eq[-1] - 1
    spy_dd = max_dd(spy_eq)

    print("===== Exit A/B Summary =====")
    print(f"{'variant':<24} {'return':>9} {'CAGR':>8} {'maxDD':>8} {'trades':>7} {'win%':>7} {'avg/tr':>8} {'med/tr':>8} {'days':>6}")
    print("-" * 94)
    for r in results:
        print(
            f"{r['name']:<24} "
            f"{r['total_return']*100:>8.1f}% "
            f"{r['cagr']*100:>7.1f}% "
            f"{r['max_dd']*100:>7.1f}% "
            f"{r['trades']:>7} "
            f"{r['win_rate']*100:>6.1f}% "
            f"{r['avg_trade']*100:>7.2f}% "
            f"{r['median_trade']*100:>7.2f}% "
            f"{r['avg_days']:>6.1f}"
        )
    print("-" * 94)
    print(f"{'SPY buy&hold':<24} {spy_ret*100:>8.1f}% {'':>7} {spy_dd*100:>7.1f}%")

    print("\n===== Exit Breakdown =====")
    for r in results:
        parts = ", ".join(f"{k}:{v}" for k, v in sorted(r["exits"].items())) or "-"
        print(f"{r['name']:<24} {parts}")

    base = results[0]
    print("\n===== Relative To Baseline =====")
    for r in results[1:]:
        print(
            f"{r['name']}: return {((r['total_return'] - base['total_return']) * 100):+.1f}pt, "
            f"maxDD {((r['max_dd'] - base['max_dd']) * 100):+.1f}pt, "
            f"trades {r['trades'] - base['trades']:+d}, "
            f"avg_days {r['avg_days'] - base['avg_days']:+.1f}"
        )


if __name__ == "__main__":
    main()
