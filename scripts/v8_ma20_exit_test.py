"""回测对比:MA20 退出的"健康回调容忍"变体 vs 现行一刀切。

买入逻辑完全不变(v8 趋势门+动量排名),只改 MA20 退出条件,隔离变量。
连续运行 2023→今(与 v8_backtest 一致),对比 总收益/最大回撤/分年/卖出次数。
运行:PYTHONPATH=. python3 scripts/v8_ma20_exit_test.py
"""
from dotenv import load_dotenv; load_dotenv(".env")
from datetime import date, timedelta
import numpy as np
import pandas as pd
from src.analysis.v8_backtest import (
    _fetch, _rsi, _max_dd, _by_year, _UNI,
    MAX_POS, STOP, TRAIL_TRIGGER, TRAIL_GIVEBACK, COST, MOM_DAYS,
)

SIM_START = "2023-01-01"
syms = sorted(set(_UNI.read_text().split()))
fetch_start = (date.fromisoformat(SIM_START) - timedelta(days=120)).isoformat()
print(f"取数 {len(syms)}+2 只 from {fetch_start} …")
px = _fetch(syms + ["SPY", "QQQ"], fetch_start)
px = px[px.index <= pd.Timestamp(date.today().isoformat())]
ma20 = px.rolling(20).mean(); ma50 = px.rolling(50).mean()
slope = ma50 - ma50.shift(5); mom = px / px.shift(MOM_DAYS) - 1
rsi = px.apply(_rsi)
A = {k: v.values for k, v in {"px": px, "ma20": ma20, "ma50": ma50, "slope": slope, "mom": mom, "rsi": rsi}.items()}
cols = list(px.columns)
bench_cols = {cols.index("SPY")} | ({cols.index("QQQ")} if "QQQ" in cols else set())
sim_idx = [j for j, d in enumerate(px.index) if d >= pd.Timestamp(SIM_START)]
sim_dates = px.index[sim_idx]
print(f"模拟 {len(sim_idx)} 天 ({str(sim_dates[0])[:10]} ~ {str(sim_dates[-1])[:10]})\n")


def run(mode, param=None):
    cash, each = 1.0, 1.0 / MAX_POS
    pos = {}   # ci -> [ent, frac, hi, below_count]
    eq = np.empty(len(sim_idx)); n_sells = 0
    for k, i in enumerate(sim_idx):
        row = A["px"][i]
        for ci in list(pos):
            p = row[ci]
            if np.isnan(p):
                continue
            ent, frac, hi, below = pos[ci]
            hi = max(hi, p)
            m20 = A["ma20"][i, ci]
            below = below + 1 if (not np.isnan(m20) and p < m20) else 0
            pos[ci][2] = hi; pos[ci][3] = below
            gain = p / ent - 1
            ma20_hit = False
            if not np.isnan(m20):
                if mode == "baseline":  ma20_hit = p < m20
                elif mode == "confirm": ma20_hit = below >= param
                elif mode == "buffer":  ma20_hit = p < m20 * (1 - param)
            if gain <= -STOP or (gain >= TRAIL_TRIGGER and p <= hi * (1 - TRAIL_GIVEBACK)) or ma20_hit:
                cash += frac * (p / ent) * (1 - COST); del pos[ci]; n_sells += 1
        free = MAX_POS - len(pos)
        if free > 0 and cash > each * 0.5:
            elig = (row > A["ma50"][i]) & (A["slope"][i] > 0) & (A["rsi"][i] >= 50) & (A["rsi"][i] <= 80) \
                   & (A["mom"][i] > 0) & (row <= A["ma20"][i] * 1.15) & ~np.isnan(row)
            for bc in bench_cols:
                elig[bc] = False
            for ci in pos:
                elig[ci] = False
            idx = np.where(elig)[0]
            if len(idx):
                for ci in idx[np.argsort(-A["mom"][i][idx])][:free]:
                    if cash < each * 0.5:
                        break
                    spend = min(each, cash); pos[ci] = [row[ci], spend * (1 - COST), row[ci], 0]; cash -= spend
        val = cash
        for ci, (ent, frac, hi, below) in pos.items():
            p = row[ci]
            if not np.isnan(p):
                val += frac * (p / ent)
        eq[k] = val
    return eq, n_sells


def bench(name):
    s = px[name].values[sim_idx]; beq = s / s[0]
    return (beq[-1] - 1) * 100, _max_dd(beq) * 100, _by_year(sim_dates, beq)


VARIANTS = [
    ("A 基线(收盘<MA20)",      "baseline", None),
    ("B 连续2根<MA20",          "confirm",  2),
    ("B 连续3根<MA20",          "confirm",  3),
    ("C 缓冲带 破2%",           "buffer",   0.02),
    ("C 缓冲带 破3%",           "buffer",   0.03),
]
print(f"{'变体':<22}{'总收益':>9}{'最大回撤':>10}{'卖出次数':>9}  分年")
print("-" * 78)
for label, mode, param in VARIANTS:
    eq, n = run(mode, param)
    tr = (eq[-1] - 1) * 100; dd = _max_dd(eq) * 100
    by = _by_year(sim_dates, eq)
    byr = " ".join(f"{y}:{v:+.0f}%" for y, v in by.items())
    print(f"{label:<22}{tr:>+8.0f}%{dd:>9.0f}%{n:>9}  {byr}")
print("-" * 78)
for nm in ["SPY", "QQQ"]:
    tr, dd, by = bench(nm)
    byr = " ".join(f"{y}:{v:+.0f}%" for y, v in by.items())
    print(f"{nm:<22}{tr:>+8.0f}%{dd:>9.0f}%{'—':>9}  {byr}")
