#!/usr/bin/env python3
"""v8 趋势打法 — 稳健性检查:一次取数,跑多组参数,看是否大多数都赢 SPY。
不是为了找最优参数,是确认"赢 SPY"不依赖某一组幸运参数(防过拟合)。
网格:动量周期 {40,60,120} × 仓位 {8,10,15}(止损/追踪固定)。
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from dotenv import load_dotenv
load_dotenv()

import numpy as np
import pandas as pd
from src.trader.alpaca_trader import get_client

START = "2022-09-01"
SIM_START = "2023-01-01"
STOP = 0.08
TRAIL_TRIGGER = 0.06
TRAIL_GIVEBACK = 0.08
COST = 0.0005
MOM_PERIODS = [40, 60, 120]
POS_GRID = [8, 10, 15]


def fetch_closes(symbols, start):
    client = get_client()
    closes = {}
    for i in range(0, len(symbols), 50):
        chunk = symbols[i:i + 50]
        try:
            df = client.get_bars(chunk, "1Day", start=start, adjustment="all", feed="iex").df
        except Exception:
            continue
        if df is None or len(df) == 0:
            continue
        if "symbol" in df.columns:
            for sym, g in df.groupby("symbol"):
                closes[sym] = g["close"]
        elif isinstance(df.index, pd.MultiIndex):
            for sym in df.index.get_level_values(0).unique():
                closes[sym] = df.loc[sym]["close"]
        else:
            closes[chunk[0]] = df["close"]
    px = pd.DataFrame(closes)
    px.index = pd.to_datetime(px.index).tz_localize(None)
    return px.sort_index()


def rsi(series, n=14):
    d = series.diff()
    up = d.clip(lower=0).ewm(alpha=1/n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1/n, adjust=False).mean()
    return 100 - 100 / (1 + up / dn.replace(0, 1e-9))


def max_dd(eq):
    peak = np.maximum.accumulate(eq)
    return float(((eq - peak) / peak).min())


def run_sim(px_a, ma20_a, ma50_a, slope_a, rsi_a, mom_a, sim_idx, max_pos):
    each = 1.0 / max_pos
    cash = 1.0
    pos = {}   # col -> [entry, frac, hi]
    eq = np.empty(len(sim_idx))
    for k, i in enumerate(sim_idx):
        row = px_a[i]
        # exits
        for ci in list(pos):
            p = row[ci]
            if np.isnan(p):
                continue
            entry, frac, hi = pos[ci]
            hi = max(hi, p); pos[ci][2] = hi
            gain = p / entry - 1
            m20 = ma20_a[i, ci]
            if gain <= -STOP or (gain >= TRAIL_TRIGGER and p <= hi * (1 - TRAIL_GIVEBACK)) \
               or (not np.isnan(m20) and p < m20):
                cash += frac * (p / entry) * (1 - COST)
                del pos[ci]
        # entries
        free = max_pos - len(pos)
        if free > 0 and cash > each * 0.5:
            elig = (row > ma50_a[i]) & (slope_a[i] > 0) & (rsi_a[i] >= 50) & (rsi_a[i] <= 80) \
                   & (mom_a[i] > 0) & (row <= ma20_a[i] * 1.15) & ~np.isnan(row)
            for ci in pos:
                elig[ci] = False
            idx = np.where(elig)[0]
            if len(idx):
                idx = idx[np.argsort(-mom_a[i][idx])][:free]
                for ci in idx:
                    if cash < each * 0.5:
                        break
                    spend = min(each, cash)
                    pos[ci] = [row[ci], spend * (1 - COST), row[ci]]
                    cash -= spend
        val = cash
        for ci, (entry, frac, hi) in pos.items():
            p = row[ci]
            if not np.isnan(p):
                val += frac * (p / entry)
        eq[k] = val
    return eq


def main():
    uf = Path(__file__).resolve().parent.parent / "data" / "sp500_constituents.txt"
    symbols = sorted(set(uf.read_text().split()))
    print(f"股票池 {len(symbols)} 只 | 网格 动量{MOM_PERIODS} × 仓位{POS_GRID}")
    print("取数中…")
    px = fetch_closes(symbols, START)
    spy = fetch_closes(["SPY"], START)["SPY"]
    print(f"取到 {px.shape[1]} 只 × {px.shape[0]} 天")

    ma20 = px.rolling(20).mean()
    ma50 = px.rolling(50).mean()
    slope = ma50 - ma50.shift(5)
    rsi_p = px.apply(rsi)
    moms = {n: (px / px.shift(n) - 1) for n in MOM_PERIODS}

    px_a, ma20_a, ma50_a, slope_a, rsi_a = (x.values for x in (px, ma20, ma50, slope, rsi_p))
    all_dates = px.index
    sim_idx = [j for j, d in enumerate(all_dates) if d >= pd.Timestamp(SIM_START)]
    sim_dates = all_dates[sim_idx]

    spy_s = spy[spy.index >= pd.Timestamp(SIM_START)]
    spy_ret = spy_s.iloc[-1] / spy_s.iloc[0] - 1
    spy_dd = max_dd((spy_s / spy_s.iloc[0]).values)

    print(f"\nSPY 基准:  总收益 {spy_ret*100:+.0f}%   最大回撤 {spy_dd*100:.0f}%\n")
    print(f"{'动量周期':>6} {'仓位':>4} | {'总收益':>8} {'最大回撤':>8} {'vs SPY':>8}")
    print("-" * 44)
    wins = 0; total = 0
    for n in MOM_PERIODS:
        mom_a = moms[n].values
        for pos_n in POS_GRID:
            eq = run_sim(px_a, ma20_a, ma50_a, slope_a, rsi_a, mom_a, sim_idx, pos_n)
            tot = eq[-1] - 1
            dd = max_dd(eq)
            beat = tot - spy_ret
            total += 1; wins += 1 if beat > 0 else 0
            flag = "✓赢" if beat > 0 else "✗输"
            print(f"{n:>5}d {pos_n:>4} | {tot*100:>7.0f}% {dd*100:>7.0f}% {beat*100:>+7.0f}% {flag}")
    print("-" * 44)
    print(f"\n{wins}/{total} 组赢 SPY。")
    print("判读:多数(≥7/9)赢 → edge 稳健,可上线;只有零星赢 → 过拟合,别信。")


if __name__ == "__main__":
    main()
