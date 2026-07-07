#!/usr/bin/env python3
"""AI hardware-vs-software rotation overlay backtest.

Analysis only: does not place orders or change live strategy.

Compares:
- baseline_v10: v10-style mechanical momentum entries + v9 BE5/trail5 exits
- soft_rotation: when software leadership is confirmed, prefer SaaS candidates
  and require stronger hardware confirmation
- hard_hw_pause: when software leadership is confirmed, pause new hardware buys
- soft_rotation_capped: prefer SaaS before top-N cut, but cap sector crowding
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter, defaultdict
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
CACHE_FILE = Path(__file__).resolve().parent.parent / "data" / "ai_rotation_ohlc_cache.pkl"

HARDWARE = {
    "NVDA", "AMD", "AVGO", "MU", "MRVL", "LRCX", "AMAT", "KLAC",
    "ACLS", "ONTO", "ARM", "SMCI", "QCOM", "TXN", "ON", "FORM",
}
SOFTWARE = {
    "DDOG", "SNOW", "CRM", "NOW", "PANW", "CRWD", "ZS", "MDB",
    "NET", "TEAM", "WDAY", "OKTA", "GTLB", "MNDY", "BILL", "ADBE",
}


@dataclass(frozen=True)
class Variant:
    name: str
    mode: str = "baseline"  # baseline | soft | hard
    accel_mult: float = 1.0
    accel_fast_exit: bool = False
    profit_takes: tuple[tuple[float, float], ...] = ()


VARIANTS = [
    Variant("baseline_v10", "baseline"),
    Variant("soft_rotation", "soft"),
    Variant("soft_rotation_capped", "capped"),
    Variant("hard_hw_pause", "hard"),
    Variant("accel_size_1_25x", "capped", accel_mult=1.25),
    Variant("accel_1_25x_fast_exit", "capped", accel_mult=1.25, accel_fast_exit=True),
    Variant("partial_tp6_1_3", "capped", profit_takes=((0.06, 1 / 3),)),
    Variant("partial_tp6_tp10", "capped", profit_takes=((0.06, 1 / 3), (0.10, 1 / 3))),
]


def fetch_ohlc(symbols: list[str], start: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    symbols = sorted(set(symbols))
    if CACHE_FILE.exists():
        try:
            cached = pd.read_pickle(CACHE_FILE)
            close, high = cached["close"], cached["high"]
            missing = [s for s in symbols if s not in close.columns]
            if not missing and not close.empty and "SPY" in close.columns:
                print(f"Loaded cached OHLC: {close.shape[1]} symbols x {close.shape[0]} days", flush=True)
                return close[symbols], high.reindex(close.index)[symbols]
            print(f"  [cache] missing {len(missing)} symbols, refreshing", flush=True)
        except Exception as e:
            print(f"  [warn] cache read failed: {e}", flush=True)

    client = get_client()
    closes: dict[str, pd.Series] = {}
    highs: dict[str, pd.Series] = {}

    for i in range(0, len(symbols), 50):
        chunk = symbols[i:i + 50]
        print(f"  fetch chunk {i//50 + 1}/{(len(symbols) + 49)//50}: {chunk[0]}...{chunk[-1]}", flush=True)
        try:
            df = client.get_bars(chunk, "1Day", start=start, adjustment="all", feed="iex").df
        except Exception as e:
            print(f"  [warn] batch failed, trying one-by-one: {e}", flush=True)
            for sym in chunk:
                try:
                    df1 = client.get_bars(sym, "1Day", start=start, adjustment="all", feed="iex").df
                    if df1 is not None and len(df1):
                        closes[sym] = df1["close"]
                        highs[sym] = df1["high"]
                except Exception as e1:
                    print(f"    [skip] {sym}: {e1}", flush=True)
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
            sym = chunk[0]
            closes[sym] = df["close"]
            highs[sym] = df["high"]

    close = pd.DataFrame(closes).sort_index()
    high = pd.DataFrame(highs).sort_index()
    close.index = pd.to_datetime(close.index).tz_localize(None)
    high.index = pd.to_datetime(high.index).tz_localize(None)
    close = close.dropna(axis=1, how="all")
    high = high.reindex(close.index)[close.columns]
    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    pd.to_pickle({"close": close, "high": high}, CACHE_FILE)
    return close, high


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


def profit_factor(rets: np.ndarray) -> float:
    wins = rets[rets > 0].sum()
    losses = rets[rets < 0].sum()
    if losses == 0:
        return float("inf") if wins > 0 else 0.0
    return float(wins / abs(losses))


def group_for(sym: str) -> str:
    if sym in HARDWARE:
        return "hardware"
    if sym in SOFTWARE:
        return "software"
    return "other"


def capped_overlay_order(idx: np.ndarray, mom_row: np.ndarray, cols: list[str],
                         slots: int, max_software: int = 6, max_hardware: int = 3) -> list[int]:
    """Sector-aware overlay order with simple concentration caps."""
    buckets = {"software": [], "other": [], "hardware": []}
    for ci in idx:
        buckets[group_for(cols[ci])].append(ci)
    for key in buckets:
        buckets[key].sort(key=lambda ci: mom_row[ci], reverse=True)

    selected: list[int] = []
    counts = Counter()
    for key in ("software", "other", "hardware"):
        limit = max_software if key == "software" else max_hardware if key == "hardware" else slots
        for ci in buckets[key]:
            if len(selected) >= slots:
                return selected
            if counts[key] >= limit:
                continue
            selected.append(ci)
            counts[key] += 1

    return selected


def build_rotation_signal(close: pd.DataFrame) -> pd.Series:
    hw = [s for s in HARDWARE if s in close.columns]
    sw = [s for s in SOFTWARE if s in close.columns]
    if not hw or not sw:
        return pd.Series(False, index=close.index)

    ret1 = close / close.shift(1) - 1
    ret3 = close / close.shift(3) - 1
    ma20 = close.rolling(20).mean()

    sw_minus_hw_3d = ret3[sw].median(axis=1) - ret3[hw].median(axis=1)
    hw_1d = ret1[hw].median(axis=1)
    sw_breadth = (close[sw] > ma20[sw]).mean(axis=1)
    hw_breadth = (close[hw] > ma20[hw]).mean(axis=1)

    signal = (
        (sw_minus_hw_3d > 0.03)
        & (hw_1d < -0.02)
        & (sw_breadth > hw_breadth)
    )
    return signal.fillna(False)


def run_variant(
    variant: Variant,
    close: pd.DataFrame,
    high: pd.DataFrame,
    sim_dates: pd.DatetimeIndex,
    rotation_on: pd.Series,
) -> dict:
    ma20 = close.rolling(20).mean()
    ma50 = close.rolling(50).mean()
    slope = ma50 - ma50.shift(5)
    mom = close / close.shift(MOM_DAYS) - 1
    ret1 = close / close.shift(1) - 1
    ret5 = close / close.shift(5) - 1
    rsi_p = close.apply(rsi)

    px_a = close.values
    hi_a = high.values
    ma20_a = ma20.values
    ma50_a = ma50.values
    slope_a = slope.values
    mom_a = mom.values
    ret1_a = ret1.values
    ret5_a = ret5.values
    rsi_a = rsi_p.values
    cols = list(close.columns)

    hw_mask = np.array([c in HARDWARE for c in cols])
    sw_mask = np.array([c in SOFTWARE for c in cols])

    sim_date_set = set(sim_dates)
    sim_idx = [i for i, d in enumerate(close.index) if d in sim_date_set]
    each = 1.0 / MAX_POS
    cash = 1.0
    positions: dict[int, dict] = {}
    eq = np.empty(len(sim_idx))
    trades: list[dict] = []
    exit_counts: Counter[str] = Counter()
    entries_by_group: Counter[str] = Counter()
    overlay_entries: Counter[str] = Counter()
    accel_entries = 0

    for k, i in enumerate(sim_idx):
        row = px_a[i]
        high_row = hi_a[i]
        overlay = bool(rotation_on.iloc[i])

        for ci in list(positions):
            p = row[ci]
            day_high = high_row[ci]
            if np.isnan(p):
                continue
            pos = positions[ci]
            pos["hi"] = max(pos["hi"], day_high if not np.isnan(day_high) else p)

            entry = pos["entry"]
            gain_close = p / entry - 1
            gain_high = pos["hi"] / entry - 1
            below2 = (
                i >= 1
                and not np.isnan(ma20_a[i, ci])
                and not np.isnan(ma20_a[i - 1, ci])
                and p < ma20_a[i, ci]
                and px_a[i - 1, ci] < ma20_a[i - 1, ci]
            )

            reason = None
            exit_price = p
            for level, sell_frac in variant.profit_takes:
                take_key = f"tp_{level:.2f}"
                if gain_high >= level and take_key not in pos["partial_taken"]:
                    sell_amount = min(pos["frac"], pos["orig_frac"] * sell_frac)
                    if sell_amount > 1e-9:
                        cash += sell_amount * (p / entry) * (1 - COST)
                        pos["frac"] -= sell_amount
                        pos["partial_taken"].add(take_key)
                        ret = p / entry - 1
                        reason_key = f"partial_tp_{int(level * 100)}"
                        trades.append({
                            "symbol": cols[ci],
                            "group": group_for(cols[ci]),
                            "ret": ret,
                            "days": k - pos["entry_k"] + 1,
                            "reason": reason_key,
                            "overlay_entry": pos["overlay_entry"],
                            "accel_entry": pos.get("accel_entry", False),
                        })
                        exit_counts[reason_key] += 1

            if gain_close <= -STOP:
                reason = "hard_stop"
            elif pos.get("breakeven_active") and p <= entry:
                reason = "breakeven"
                exit_price = entry
            elif (
                variant.accel_fast_exit
                and pos.get("accel_entry")
                and gain_high >= 0.04
                and p <= pos["hi"] * 0.97
            ):
                reason = "accel_fast_trail"
            elif gain_high >= 0.06 and p <= pos["hi"] * 0.95:
                reason = "trail"
            elif below2:
                reason = "ma20_2d"

            if not pos.get("breakeven_active") and gain_high >= 0.05:
                pos["breakeven_active"] = True

            if reason:
                cash += pos["frac"] * (exit_price / entry) * (1 - COST)
                ret = exit_price / entry - 1
                trades.append({
                    "symbol": cols[ci],
                    "group": group_for(cols[ci]),
                    "ret": ret,
                    "days": k - pos["entry_k"] + 1,
                    "reason": reason,
                    "overlay_entry": pos["overlay_entry"],
                    "accel_entry": pos.get("accel_entry", False),
                })
                exit_counts[reason] += 1
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

            if overlay and variant.mode == "soft":
                elig &= ~(hw_mask & ((rsi_a[i] < 52) | (row < ma20_a[i])))
            elif overlay and variant.mode == "capped":
                elig &= ~(hw_mask & ((rsi_a[i] < 52) | (row < ma20_a[i])))
            elif overlay and variant.mode == "hard":
                elig &= ~hw_mask

            for ci in positions:
                elig[ci] = False

            idx = np.where(elig)[0]
            if len(idx):
                if overlay and variant.mode == "soft":
                    priority = np.where(sw_mask[idx], 0, np.where(hw_mask[idx], 2, 1))
                    order = np.lexsort((-mom_a[i][idx], priority))
                    idx = idx[order]
                elif overlay and variant.mode == "capped":
                    idx = np.array(capped_overlay_order(idx, mom_a[i], cols, free), dtype=int)
                else:
                    idx = idx[np.argsort(-mom_a[i][idx])]
                idx = idx[:free]

                for ci in idx:
                    if cash < each * 0.5:
                        break
                    accel = (
                        variant.accel_mult > 1.0
                        and not np.isnan(ret1_a[i, ci])
                        and not np.isnan(ret5_a[i, ci])
                        and not np.isnan(ma20_a[i, ci])
                        and ret1_a[i, ci] >= 0.03
                        and ret5_a[i, ci] >= 0.05
                        and rsi_a[i, ci] <= 78
                        and row[ci] <= ma20_a[i, ci] * 1.12
                    )
                    spend = min(each * (variant.accel_mult if accel else 1.0), cash)
                    grp = group_for(cols[ci])
                    positions[ci] = {
                        "entry": row[ci],
                        "frac": spend * (1 - COST),
                        "orig_frac": spend * (1 - COST),
                        "hi": row[ci],
                        "entry_k": k,
                        "overlay_entry": overlay,
                        "accel_entry": accel,
                        "partial_taken": set(),
                    }
                    cash -= spend
                    entries_by_group[grp] += 1
                    if accel:
                        accel_entries += 1
                    if overlay:
                        overlay_entries[grp] += 1

        val = cash
        for ci, pos in positions.items():
            p = row[ci]
            if not np.isnan(p):
                val += pos["frac"] * (p / pos["entry"])
        eq[k] = val

    trade_rets = np.array([t["ret"] for t in trades], dtype=float)
    trade_days = np.array([t["days"] for t in trades], dtype=float)
    by_group = {}
    for grp in ("hardware", "software", "other"):
        rs = np.array([t["ret"] for t in trades if t["group"] == grp], dtype=float)
        by_group[grp] = {
            "trades": int(len(rs)),
            "avg": float(rs.mean()) if len(rs) else 0.0,
            "win": float((rs > 0).mean()) if len(rs) else 0.0,
            "pf": profit_factor(rs) if len(rs) else 0.0,
        }

    overlay_rs = np.array([t["ret"] for t in trades if t["overlay_entry"]], dtype=float)
    accel_rs = np.array([t["ret"] for t in trades if t.get("accel_entry")], dtype=float)
    return {
        "name": variant.name,
        "eq": eq,
        "total_return": eq[-1] - 1,
        "cagr": cagr(eq, sim_dates),
        "max_dd": max_dd(eq),
        "trades": len(trades),
        "win_rate": float((trade_rets > 0).mean()) if len(trade_rets) else 0.0,
        "profit_factor": profit_factor(trade_rets) if len(trade_rets) else 0.0,
        "avg_trade": float(trade_rets.mean()) if len(trade_rets) else 0.0,
        "median_trade": float(np.median(trade_rets)) if len(trade_rets) else 0.0,
        "avg_days": float(trade_days.mean()) if len(trade_days) else 0.0,
        "entries_by_group": dict(entries_by_group),
        "overlay_entries": dict(overlay_entries),
        "overlay_avg_trade": float(overlay_rs.mean()) if len(overlay_rs) else 0.0,
        "accel_entries": accel_entries,
        "accel_avg_trade": float(accel_rs.mean()) if len(accel_rs) else 0.0,
        "accel_win_rate": float((accel_rs > 0).mean()) if len(accel_rs) else 0.0,
        "by_group": by_group,
        "exits": dict(exit_counts),
    }


def load_universe() -> list[str]:
    root = Path(__file__).resolve().parent.parent
    uni = root / "data" / "sp500_constituents.txt"
    symbols = set(uni.read_text().split()) if uni.exists() else set()
    symbols |= HARDWARE | SOFTWARE | {"SPY"}
    return sorted(symbols)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default=SIM_START)
    args = parser.parse_args()

    symbols = load_universe()
    print(f"Universe: {len(symbols)} symbols | fetch from {START} | simulate from {args.start}", flush=True)
    print("Fetching daily bars from Alpaca IEX...", flush=True)
    close, high = fetch_ohlc(symbols, START)
    if close.empty or "SPY" not in close.columns:
        raise SystemExit("No data or missing SPY")

    close = close[close.index <= pd.Timestamp(date.today().isoformat())]
    high = high.reindex_like(close)
    sim_dates = close.index[close.index >= pd.Timestamp(args.start)]
    strategy_close = close.drop(columns=["SPY"], errors="ignore")
    strategy_high = high[strategy_close.columns]

    rotation_on = build_rotation_signal(strategy_close)
    rot_days = int(rotation_on.reindex(sim_dates).fillna(False).sum())
    print(f"Fetched: {strategy_close.shape[1]} symbols x {strategy_close.shape[0]} days", flush=True)
    print(f"Sim dates: {sim_dates[0].date()} ~ {sim_dates[-1].date()} ({len(sim_dates)} bars)")
    print(f"Rotation days: {rot_days}/{len(sim_dates)} ({rot_days / len(sim_dates) * 100:.1f}%)\n")

    results = [run_variant(v, strategy_close, strategy_high, sim_dates, rotation_on) for v in VARIANTS]

    spy = close["SPY"].reindex(sim_dates).dropna()
    spy_eq = (spy / spy.iloc[0]).values
    spy_ret = spy_eq[-1] - 1
    spy_dd = max_dd(spy_eq)

    print("===== AI Rotation A/B Summary =====")
    print(f"{'variant':<18} {'return':>9} {'CAGR':>8} {'maxDD':>8} {'trades':>7} {'win%':>7} {'PF':>6} {'avg/tr':>8} {'days':>6}")
    print("-" * 88)
    for r in results:
        print(
            f"{r['name']:<18} "
            f"{r['total_return']*100:>8.1f}% "
            f"{r['cagr']*100:>7.1f}% "
            f"{r['max_dd']*100:>7.1f}% "
            f"{r['trades']:>7} "
            f"{r['win_rate']*100:>6.1f}% "
            f"{r['profit_factor']:>6.2f} "
            f"{r['avg_trade']*100:>7.2f}% "
            f"{r['avg_days']:>6.1f}"
        )
    print("-" * 88)
    print(f"{'SPY buy&hold':<18} {spy_ret*100:>8.1f}% {'':>7} {spy_dd*100:>7.1f}%")

    print("\n===== Group Trade Quality =====")
    for r in results:
        print(
            f"\n{r['name']}: entries={r['entries_by_group']} "
            f"overlay_entries={r['overlay_entries']} overlay_avg={r['overlay_avg_trade']*100:+.2f}% "
            f"accel_entries={r['accel_entries']} accel_avg={r['accel_avg_trade']*100:+.2f}% "
            f"accel_win={r['accel_win_rate']*100:.1f}%"
        )
        for grp, g in r["by_group"].items():
            print(f"  {grp:<8} trades={g['trades']:>3} avg={g['avg']*100:+.2f}% win={g['win']*100:>5.1f}% PF={g['pf']:.2f}")

    print("\n===== Relative To Baseline =====")
    base = results[0]
    for r in results[1:]:
        print(
            f"{r['name']}: return {(r['total_return'] - base['total_return']) * 100:+.1f}pt, "
            f"maxDD {(r['max_dd'] - base['max_dd']) * 100:+.1f}pt, "
            f"PF {r['profit_factor'] - base['profit_factor']:+.2f}, "
            f"trades {r['trades'] - base['trades']:+d}"
        )

    print("\n===== Exit Breakdown =====")
    for r in results:
        parts = ", ".join(f"{k}:{v}" for k, v in sorted(r["exits"].items())) or "-"
        print(f"{r['name']:<18} {parts}")


if __name__ == "__main__":
    main()
