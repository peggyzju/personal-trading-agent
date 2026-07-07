#!/usr/bin/env python3
"""Compare candidate ranking formulas under the current mechanical trend system.

This is research-only: it does not change Scout/Rex production logic.
It keeps the same entry gate and exit framework, then swaps only the ordering
score used after a symbol passes the gate.

Usage:
  python3 scripts/ranking_quality_backtest.py
"""
from __future__ import annotations

import sys
from pathlib import Path
import argparse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
load_dotenv()

import numpy as np
import pandas as pd

from src.monitor.sp500_scanner import (
    LAYER2_TICKERS,
    SECTOR_MAP,
    get_nasdaq100_tickers,
    get_sp500_tickers,
)
from src.trader.alpaca_trader import get_client


START = "2022-09-01"
SIM_START = "2023-01-01"
MAX_POS = 10
COST = 0.0005

HARD_STOP = -0.08
EARLY_FAILURE_STOP = -0.05
EARLY_FAILURE_MIN_DAY = 1
EARLY_FAILURE_MAX_DAY = 2
TRAIL_ACTIVATE = 0.06
TRAIL_GIVEBACK = 0.05


def fetch_ohlcv(symbols: list[str], start: str, batch_size: int) -> tuple[pd.DataFrame, pd.DataFrame]:
    client = get_client()
    closes: dict[str, pd.Series] = {}
    volumes: dict[str, pd.Series] = {}
    for i in range(0, len(symbols), batch_size):
        chunk = symbols[i:i + batch_size]
        print(f"  fetching {i + 1:>3}-{min(i + batch_size, len(symbols)):>3}/{len(symbols)}", flush=True)
        try:
            df = client.get_bars(chunk, "1Day", start=start, adjustment="all", feed="iex").df
        except Exception as exc:
            print(f"  [warn] batch {chunk[:3]}... failed: {exc}")
            continue
        if df is None or len(df) == 0:
            continue
        if "symbol" in df.columns:
            for sym, g in df.groupby("symbol"):
                closes[sym] = g["close"]
                volumes[sym] = g["volume"]
        elif isinstance(df.index, pd.MultiIndex):
            for sym in df.index.get_level_values(0).unique():
                g = df.loc[sym]
                closes[sym] = g["close"]
                volumes[sym] = g["volume"]
        else:
            closes[chunk[0]] = df["close"]
            volumes[chunk[0]] = df["volume"]
    px = pd.DataFrame(closes)
    vol = pd.DataFrame(volumes)
    px.index = pd.to_datetime(px.index).tz_localize(None)
    vol.index = pd.to_datetime(vol.index).tz_localize(None)
    return px.sort_index(), vol.sort_index()


def rsi(series: pd.Series, n: int = 14) -> pd.Series:
    d = series.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return 100 - 100 / (1 + up / dn.replace(0, 1e-9))


def zscore(values: np.ndarray) -> np.ndarray:
    out = np.zeros_like(values, dtype=float)
    ok = np.isfinite(values)
    if ok.sum() < 2:
        return out
    mu = np.nanmean(values[ok])
    sd = np.nanstd(values[ok])
    if sd <= 1e-9:
        return out
    out[ok] = (values[ok] - mu) / sd
    return out


def max_dd(eq: np.ndarray) -> float:
    peak = np.maximum.accumulate(eq)
    return float(((eq - peak) / peak).min()) if len(eq) else 0.0


def year_returns(dates: pd.Index, eq: np.ndarray) -> dict[int, float]:
    out = {}
    for y in sorted({d.year for d in dates}):
        idx = [i for i, d in enumerate(dates) if d.year == y]
        if len(idx) > 1:
            out[y] = eq[idx[-1]] / eq[idx[0]] - 1
    return out


def ranking_scores(
    name: str,
    i: int,
    px_a: np.ndarray,
    mom5_a: np.ndarray,
    mom20_a: np.ndarray,
    mom60_a: np.ndarray,
    ma20_a: np.ndarray,
    ma50_a: np.ndarray,
    slope_a: np.ndarray,
    rsi_a: np.ndarray,
    vol_ratio_a: np.ndarray,
) -> np.ndarray:
    if name == "legacy_3m":
        return mom60_a[i]

    z60 = zscore(mom60_a[i])
    z20 = zscore(mom20_a[i])
    z5 = zscore(mom5_a[i])

    if name == "balanced_momentum":
        return 0.50 * z60 + 0.30 * z20 + 0.20 * z5

    price = px_a[i]
    vs_ma20 = (price / ma20_a[i] - 1) * 100
    ma50_slope_pct = (slope_a[i] / ma50_a[i]) * 100
    vol_ratio = vol_ratio_a[i]

    # Prefer controlled strength: above MA20, not stretched; RSI strong but not hot.
    ma20_quality = -np.abs(np.clip(vs_ma20, -8, 18) - 5.0) / 5.0
    rsi_quality = -np.abs(np.clip(rsi_a[i], 45, 85) - 62.0) / 18.0
    vol_quality = np.clip(vol_ratio - 0.8, -1.0, 1.5)

    return (
        0.40 * z60
        + 0.25 * z20
        + 0.15 * z5
        + 0.10 * zscore(ma50_slope_pct)
        + 0.05 * ma20_quality
        + 0.03 * rsi_quality
        + 0.02 * vol_quality
    )


def run_sim(name: str, arrays: dict, sim_idx: list[int], bench_cols: set[int]) -> tuple[np.ndarray, dict[str, int]]:
    px_a = arrays["px"]
    ma20_a = arrays["ma20"]
    ma50_a = arrays["ma50"]
    slope_a = arrays["slope"]
    rsi_a = arrays["rsi"]
    mom5_a = arrays["mom5"]
    mom20_a = arrays["mom20"]
    mom60_a = arrays["mom60"]
    vol_ratio_a = arrays["vol_ratio"]

    each = 1.0 / MAX_POS
    cash = 1.0
    pos: dict[int, list[float]] = {}  # col -> [entry, frac, high, entry_k]
    eq = np.empty(len(sim_idx))
    exit_counts = {"hard": 0, "early_fail": 0, "trail": 0, "ma20": 0}

    for k, i in enumerate(sim_idx):
        row = px_a[i]

        for ci in list(pos):
            p = row[ci]
            if np.isnan(p):
                continue
            entry, frac, high, entry_k = pos[ci]
            high = max(high, p)
            pos[ci][2] = high
            gain = p / entry - 1
            age = k - int(entry_k)
            m20 = ma20_a[i, ci]
            reason = None
            if gain <= HARD_STOP:
                reason = "hard"
            elif EARLY_FAILURE_MIN_DAY <= age <= EARLY_FAILURE_MAX_DAY and gain <= EARLY_FAILURE_STOP:
                reason = "early_fail"
            elif high >= entry * (1 + TRAIL_ACTIVATE) and p <= high * (1 - TRAIL_GIVEBACK):
                reason = "trail"
            elif not np.isnan(m20) and p < m20:
                reason = "ma20"
            if reason:
                cash += frac * (p / entry) * (1 - COST)
                exit_counts[reason] += 1
                del pos[ci]

        free = MAX_POS - len(pos)
        if free > 0 and cash > each * 0.5:
            elig = (
                (row > ma50_a[i])
                & (slope_a[i] > 0)
                & (rsi_a[i] >= 50)
                & (rsi_a[i] <= 80)
                & (mom60_a[i] > 0)
                & (row <= ma20_a[i] * 1.15)
                & ~np.isnan(row)
            )
            for bc in bench_cols:
                elig[bc] = False
            for ci in pos:
                elig[ci] = False

            idx = np.where(elig)[0]
            if len(idx):
                scores = ranking_scores(name, i, px_a, mom5_a, mom20_a, mom60_a,
                                        ma20_a, ma50_a, slope_a, rsi_a, vol_ratio_a)
                ranked = idx[np.argsort(-scores[idx])][:free]
                for ci in ranked:
                    if cash < each * 0.5:
                        break
                    spend = min(each, cash)
                    pos[ci] = [row[ci], spend * (1 - COST), row[ci], float(k)]
                    cash -= spend

        val = cash
        for ci, (entry, frac, _high, _entry_k) in pos.items():
            p = row[ci]
            if not np.isnan(p):
                val += frac * (p / entry)
        eq[k] = val

    return eq, exit_counts


def summarize(name: str, dates: pd.Index, eq: np.ndarray, spy_ret: float) -> None:
    total = eq[-1] - 1
    dd = max_dd(eq)
    yrs = year_returns(dates, eq)
    yr_txt = " ".join(f"{y}:{r * 100:+.0f}%" for y, r in yrs.items())
    print(f"{name:<20} total {total * 100:+7.1f}%  mdd {dd * 100:6.1f}%  vs SPY {(total - spy_ret) * 100:+7.1f}%  {yr_txt}")


def build_universe(kind: str) -> list[str]:
    uf = Path(__file__).resolve().parent.parent / "data" / "sp500_constituents.txt"
    if kind == "full":
        sp500 = sorted(set(uf.read_text().split())) if uf.exists() else get_sp500_tickers()
        return sorted(set(sp500 + get_nasdaq100_tickers() + LAYER2_TICKERS))
    if kind == "sp500":
        return sorted(set(uf.read_text().split())) if uf.exists() else sorted(set(get_sp500_tickers()))
    # Faster, closer to where the strategy's high-momentum names usually come from.
    return sorted(set(get_nasdaq100_tickers() + LAYER2_TICKERS + list(SECTOR_MAP.keys())))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--universe", choices=["growth", "sp500", "full"], default="growth")
    ap.add_argument("--batch-size", type=int, default=20)
    args = ap.parse_args()

    symbols = build_universe(args.universe)
    symbols = symbols + ["SPY", "QQQ"]
    print(f"股票池 {len(symbols) - 2} 只 + SPY/QQQ [{args.universe}] | {SIM_START} 至今 | max_pos={MAX_POS}", flush=True)
    print("取数中...", flush=True)
    px, vol = fetch_ohlcv(symbols, START, args.batch_size)
    if px.empty or "SPY" not in px.columns:
        print("取数失败")
        return
    print(f"取到 {px.shape[1]} 只 x {px.shape[0]} 天")

    ma20 = px.rolling(20).mean()
    ma50 = px.rolling(50).mean()
    slope = ma50 - ma50.shift(5)
    rsi_p = px.apply(rsi)
    vol_ratio = vol / vol.rolling(20).mean()

    arrays = {
        "px": px.values,
        "ma20": ma20.values,
        "ma50": ma50.values,
        "slope": slope.values,
        "rsi": rsi_p.values,
        "mom5": (px / px.shift(5) - 1).values,
        "mom20": (px / px.shift(20) - 1).values,
        "mom60": (px / px.shift(60) - 1).values,
        "vol_ratio": vol_ratio.values,
    }
    cols = list(px.columns)
    spy_col = cols.index("SPY")
    qqq_col = cols.index("QQQ") if "QQQ" in cols else -1
    bench_cols = {c for c in (spy_col, qqq_col) if c >= 0}
    sim_idx = [j for j, d in enumerate(px.index) if d >= pd.Timestamp(SIM_START)]
    sim_dates = px.index[sim_idx]

    spy = px["SPY"].values[sim_idx]
    spy_eq = spy / spy[0]
    spy_ret = spy_eq[-1] - 1
    print(f"\nSPY                 total {spy_ret * 100:+7.1f}%  mdd {max_dd(spy_eq) * 100:6.1f}%")
    if "QQQ" in px.columns:
        qqq = px["QQQ"].values[sim_idx]
        qqq_eq = qqq / qqq[0]
        print(f"QQQ                 total {(qqq_eq[-1] - 1) * 100:+7.1f}%  mdd {max_dd(qqq_eq) * 100:6.1f}%")

    print("\nRanking variants:")
    for name in ("legacy_3m", "balanced_momentum", "quality_momentum"):
        eq, exits = run_sim(name, arrays, sim_idx, bench_cols)
        summarize(name, sim_dates, eq, spy_ret)
        print(f"  exits: {exits}")

    print("\n说明:")
    print("  legacy_3m = 当前排序锚，只按 60d/约3个月动量排序。")
    print("  balanced_momentum = 3M/1M/5D 组合排序。")
    print("  quality_momentum = 3M/1M/5D + MA50斜率 + MA20位置 + RSI + 量能质量。")


if __name__ == "__main__":
    main()
