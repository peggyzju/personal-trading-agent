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
FIT_HORIZON = 5
FIT_MIN_SAMPLES = 600
FIT_TRAIN_DAYS = 504
FIT_RETRAIN_DAYS = 20
FIT_ALPHA = 10.0

FIT_FEATURES = [
    "mom60",
    "mom20",
    "mom5",
    "ret3",
    "ret1",
    "rsi",
    "vs_ma20",
    "vs_ma50",
    "ma50_slope_pct",
    "vol_ratio",
    "is_sp500",
    "is_nasdaq100",
    "is_layer2",
    "is_software",
    "is_fintech",
    "is_biotech",
    "is_semis",
]

QUALITY_VARIANTS = {
    "quality_momentum": {
        "weights": (0.40, 0.25, 0.15, 0.10, 0.05, 0.03, 0.02),
        "desc": "3M/1M/5D + MA50 slope + balanced structure quality",
    },
    "recent_confirm": {
        "weights": (0.30, 0.35, 0.25, 0.05, 0.02, 0.02, 0.01),
        "desc": "higher 1M/5D weight to require recent confirmation",
    },
    "anti_chase": {
        "weights": (0.45, 0.25, 0.10, 0.10, 0.07, 0.03, 0.00),
        "desc": "keeps 3M anchor, more penalty for stretched MA20/RSI",
    },
    "trend_quality": {
        "weights": (0.35, 0.20, 0.10, 0.20, 0.10, 0.03, 0.02),
        "desc": "leans into MA50 slope and controlled MA20 structure",
    },
    "low_noise": {
        "weights": (0.45, 0.25, 0.05, 0.15, 0.07, 0.03, 0.00),
        "desc": "less 5D noise, more trend quality and anti-chase",
    },
    "quality_plus": {
        "weights": (0.35, 0.22, 0.13, 0.15, 0.08, 0.05, 0.02),
        "desc": "30% quality weight: more structure/RSI, still momentum-led",
    },
    "quality_defensive": {
        "weights": (0.32, 0.20, 0.10, 0.18, 0.10, 0.07, 0.03),
        "desc": "38% quality weight: strongest anti-chase and structure emphasis",
    },
}


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


def entry_eligible(arrays: dict, i: int, bench_cols: set[int]) -> np.ndarray:
    row = arrays["px"][i]
    elig = (
        (row > arrays["ma50"][i])
        & (arrays["slope"][i] > 0)
        & (arrays["rsi"][i] >= 50)
        & (arrays["rsi"][i] <= 80)
        & (arrays["mom60"][i] > 0)
        & (row <= arrays["ma20"][i] * 1.15)
        & ~np.isnan(row)
    )
    for bc in bench_cols:
        elig[bc] = False
    return elig


def fitted_features(arrays: dict, i: int, idx: np.ndarray) -> np.ndarray:
    px_i = arrays["px"][i, idx]
    ma20_i = arrays["ma20"][i, idx]
    ma50_i = arrays["ma50"][i, idx]
    slope_i = arrays["slope"][i, idx]

    feature_cols = [
        arrays["mom60"][i, idx],
        arrays["mom20"][i, idx],
        arrays["mom5"][i, idx],
        arrays["px"][i, idx] / arrays["px"][i - 3, idx] - 1 if i >= 3 else np.full(len(idx), np.nan),
        arrays["px"][i, idx] / arrays["px"][i - 1, idx] - 1 if i >= 1 else np.full(len(idx), np.nan),
        arrays["rsi"][i, idx] / 100.0,
        px_i / ma20_i - 1,
        px_i / ma50_i - 1,
        slope_i / ma50_i,
        arrays["vol_ratio"][i, idx],
        arrays["is_sp500"][idx],
        arrays["is_nasdaq100"][idx],
        arrays["is_layer2"][idx],
        arrays["is_software"][idx],
        arrays["is_fintech"][idx],
        arrays["is_biotech"][idx],
        arrays["is_semis"][idx],
    ]
    x = np.column_stack(feature_cols).astype(float)
    x[:, :9] = np.clip(x[:, :9], -1.0, 1.0)
    x[:, 9] = np.clip(x[:, 9], 0.0, 10.0)
    x[~np.isfinite(x)] = np.nan
    return x


def fitted_label(arrays: dict, i: int, idx: np.ndarray) -> np.ndarray:
    entry = arrays["px"][i, idx]
    future = arrays["px"][i + 1:i + FIT_HORIZON + 1, :][:, idx]
    future_ret = future[-1] / entry - 1
    path_ret = future / entry - 1
    valid = np.isfinite(path_ret).any(axis=0)
    future_dd = np.full(len(idx), np.nan)
    future_dd[valid] = np.nanmin(path_ret[:, valid], axis=0)
    # Reward upside, but punish names that first put the portfolio under pressure.
    label = future_ret + 0.7 * np.minimum(future_dd, 0)
    return np.clip(label, -0.35, 0.35)


def ridge_predict(train_x: np.ndarray, train_y: np.ndarray, pred_x: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    ok = np.isfinite(train_y) & np.isfinite(train_x).all(axis=1)
    train_x = train_x[ok]
    train_y = train_y[ok]
    pred_ok = np.isfinite(pred_x).all(axis=1)
    preds = np.full(pred_x.shape[0], np.nan)
    if len(train_y) < FIT_MIN_SAMPLES or pred_ok.sum() == 0:
        return preds, np.zeros(train_x.shape[1] if train_x.ndim == 2 else len(FIT_FEATURES))

    mu = train_x.mean(axis=0)
    sd = train_x.std(axis=0)
    sd[sd < 0.05] = 1.0
    x = (train_x - mu) / sd
    y = train_y - train_y.mean()
    x = np.clip(np.nan_to_num(x, nan=0.0, posinf=0.0, neginf=0.0), -20.0, 20.0)
    y = np.clip(np.nan_to_num(y, nan=0.0, posinf=0.0, neginf=0.0), -0.5, 0.5)
    xtx = np.einsum("ni,nj->ij", x, x, optimize=False)
    xty = np.einsum("ni,n->i", x, y, optimize=False)
    penalty = np.eye(x.shape[1]) * FIT_ALPHA
    try:
        beta = np.linalg.solve(xtx + penalty, xty)
    except np.linalg.LinAlgError:
        beta = np.linalg.lstsq(xtx + penalty, xty, rcond=None)[0]
    pred_scaled = (pred_x[pred_ok] - mu) / sd
    pred_scaled = np.clip(np.nan_to_num(pred_scaled, nan=0.0, posinf=0.0, neginf=0.0), -20.0, 20.0)
    preds[pred_ok] = np.einsum("ni,i->n", pred_scaled, beta, optimize=False)
    return preds, beta / sd


def build_fitted_scores(arrays: dict, sim_idx: list[int], bench_cols: set[int]) -> dict:
    n_days, n_cols = arrays["px"].shape
    scores = np.full((n_days, n_cols), np.nan)
    last_fit_i = -10**9
    train_x = np.empty((0, len(FIT_FEATURES)))
    train_y = np.empty(0)
    last_coef = np.zeros(len(FIT_FEATURES))
    fits = 0

    for i in sim_idx:
        if i + FIT_HORIZON >= n_days:
            continue
        if i - last_fit_i >= FIT_RETRAIN_DAYS:
            start_i = max(60, i - FIT_TRAIN_DAYS - FIT_HORIZON)
            end_i = i - FIT_HORIZON
            xs: list[np.ndarray] = []
            ys: list[np.ndarray] = []
            for j in range(start_i, end_i):
                elig = entry_eligible(arrays, j, bench_cols)
                idx = np.where(elig)[0]
                if len(idx) == 0 or j + FIT_HORIZON >= n_days:
                    continue
                xs.append(fitted_features(arrays, j, idx))
                ys.append(fitted_label(arrays, j, idx))
            train_x = np.vstack(xs) if xs else np.empty((0, len(FIT_FEATURES)))
            train_y = np.concatenate(ys) if ys else np.empty(0)
            last_fit_i = i
            fits += 1

        idx = np.where(entry_eligible(arrays, i, bench_cols))[0]
        if len(idx) == 0:
            continue
        pred_x = fitted_features(arrays, i, idx)
        preds, coef = ridge_predict(train_x, train_y, pred_x)
        scores[i, idx] = preds
        if np.any(coef):
            last_coef = coef

    return {"scores": scores, "last_coef": last_coef, "fits": fits}


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

    if name not in QUALITY_VARIANTS:
        raise ValueError(f"unknown ranking variant: {name}")

    price = px_a[i]
    vs_ma20 = (price / ma20_a[i] - 1) * 100
    ma50_slope_pct = (slope_a[i] / ma50_a[i]) * 100
    vol_ratio = vol_ratio_a[i]

    # Prefer controlled strength: above MA20, not stretched; RSI strong but not hot.
    ma20_quality = -np.abs(np.clip(vs_ma20, -8, 18) - 5.0) / 5.0
    rsi_quality = -np.abs(np.clip(rsi_a[i], 45, 85) - 62.0) / 18.0
    vol_quality = np.clip(vol_ratio - 0.8, -1.0, 1.5)
    w60, w20, w5, w_slope, w_ma20, w_rsi, w_vol = QUALITY_VARIANTS[name]["weights"]

    return (
        w60 * z60
        + w20 * z20
        + w5 * z5
        + w_slope * zscore(ma50_slope_pct)
        + w_ma20 * ma20_quality
        + w_rsi * rsi_quality
        + w_vol * vol_quality
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
            elig = entry_eligible(arrays, i, bench_cols)
            for ci in pos:
                elig[ci] = False

            idx = np.where(elig)[0]
            if len(idx):
                if name == "fitted_ridge_5d":
                    scores = arrays["fit_score"][i]
                else:
                    scores = ranking_scores(name, i, px_a, mom5_a, mom20_a, mom60_a,
                                            ma20_a, ma50_a, slope_a, rsi_a, vol_ratio_a)
                idx = idx[np.isfinite(scores[idx])]
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


def print_top_candidates(name: str, arrays: dict, cols: list[str], sim_idx: list[int], bench_cols: set[int], limit: int = 15) -> None:
    px_a = arrays["px"]
    i = sim_idx[-1]
    elig = entry_eligible(arrays, i, bench_cols)
    idx = np.where(elig)[0]
    if len(idx) == 0:
        print(f"  {name:<20} top: none")
        return
    if name == "fitted_ridge_5d":
        scores = arrays["fit_score"][i]
    else:
        scores = ranking_scores(
            name,
            i,
            px_a,
            arrays["mom5"],
            arrays["mom20"],
            arrays["mom60"],
            arrays["ma20"],
            arrays["ma50"],
            arrays["slope"],
            arrays["rsi"],
            arrays["vol_ratio"],
        )
    idx = idx[np.isfinite(scores[idx])]
    ranked = idx[np.argsort(-scores[idx])][:limit]
    names = []
    for ci in ranked:
        sym = cols[ci]
        sector = SECTOR_MAP.get(sym, "OTHER")
        names.append(f"{sym}({sector})")
    print(f"  {name:<20} top{limit}: " + ", ".join(names))


def build_universe(kind: str) -> list[str]:
    uf = Path(__file__).resolve().parent.parent / "data" / "sp500_constituents.txt"
    if kind == "full":
        sp500 = sorted(set(uf.read_text().split())) if uf.exists() else get_sp500_tickers()
        return sorted(set(sp500 + get_nasdaq100_tickers() + LAYER2_TICKERS))
    if kind == "sp500":
        return sorted(set(uf.read_text().split())) if uf.exists() else sorted(set(get_sp500_tickers()))
    # Faster, closer to where the strategy's high-momentum names usually come from.
    return sorted(set(get_nasdaq100_tickers() + LAYER2_TICKERS + list(SECTOR_MAP.keys())))


def local_sp500_set() -> set[str]:
    uf = Path(__file__).resolve().parent.parent / "data" / "sp500_constituents.txt"
    return set(uf.read_text().split()) if uf.exists() else set()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--universe", choices=["growth", "sp500", "full"], default="growth")
    ap.add_argument("--batch-size", type=int, default=20)
    ap.add_argument("--skip-fit", action="store_true", help="skip fitted_ridge_5d to focus on mechanical weight sweeps")
    ap.add_argument("--top", type=int, default=15, help="number of latest eligible candidates to print per ranking variant")
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
    sp500_set = local_sp500_set()
    nasdaq_set = set(get_nasdaq100_tickers())
    layer2_set = set(LAYER2_TICKERS)
    sector_vals = [SECTOR_MAP.get(sym, "OTHER") for sym in cols]
    arrays.update({
        "is_sp500": np.array([sym in sp500_set for sym in cols], dtype=float),
        "is_nasdaq100": np.array([sym in nasdaq_set for sym in cols], dtype=float),
        "is_layer2": np.array([sym in layer2_set for sym in cols], dtype=float),
        "is_software": np.array([v == "SOFTWARE" for v in sector_vals], dtype=float),
        "is_fintech": np.array([v == "FINTECH" for v in sector_vals], dtype=float),
        "is_biotech": np.array([v == "BIOTECH" for v in sector_vals], dtype=float),
        "is_semis": np.array([v == "SEMIS" for v in sector_vals], dtype=float),
    })
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

    fit = None
    if not args.skip_fit:
        print("\n训练 fitted_ridge_5d 排序分数...", flush=True)
        fit = build_fitted_scores(arrays, sim_idx, bench_cols)
        arrays["fit_score"] = fit["scores"]
        print(f"  walk-forward refits: {fit['fits']} | horizon={FIT_HORIZON}d | train_days={FIT_TRAIN_DAYS}")

    print("\nRanking variants:")
    variants = ["legacy_3m", "balanced_momentum", *QUALITY_VARIANTS.keys()]
    if not args.skip_fit:
        variants.append("fitted_ridge_5d")
    for name in variants:
        eq, exits = run_sim(name, arrays, sim_idx, bench_cols)
        summarize(name, sim_dates, eq, spy_ret)
        print(f"  exits: {exits}")

    print(f"\nLatest eligible top lists ({sim_dates[-1].date()}):")
    for name in variants:
        print_top_candidates(name, arrays, cols, sim_idx, bench_cols, args.top)

    coef = fit["last_coef"] if fit else np.zeros(len(FIT_FEATURES))
    if np.any(coef):
        order = np.argsort(-np.abs(coef))[:8]
        print("\nFitted last-model feature weights:")
        for j in order:
            print(f"  {FIT_FEATURES[j]:<15} {coef[j]:+9.4f}")

    print("\n说明:")
    print("  legacy_3m = 当前排序锚，只按 60d/约3个月动量排序。")
    print("  balanced_momentum = 3M/1M/5D 组合排序。")
    for name, cfg in QUALITY_VARIANTS.items():
        weights = "/".join(f"{w:.2f}" for w in cfg["weights"])
        print(f"  {name} = {cfg['desc']} | weights={weights}")
    print("  fitted_ridge_5d = 机械入池后，用过去约2年样本拟合未来5日收益-回撤惩罚，只改变排序。")


if __name__ == "__main__":
    main()
