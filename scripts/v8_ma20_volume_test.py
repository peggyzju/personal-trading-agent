"""回测对比:MA20 退出 加不加「放量确认」。

在"连续2根收盘<MA20"基础上,再要求破位当天 放量(RVOL≥阈值)才卖;
缩量破位先扛(由 -8% 硬止损兜底)。把成交量喂进回测引擎。
买入逻辑完全不变,只改 MA20 退出,隔离变量。连续运行 2023→今。
运行:PYTHONPATH=. python3 scripts/v8_ma20_volume_test.py
"""
from dotenv import load_dotenv; load_dotenv(".env")
from datetime import date, timedelta
import numpy as np
import pandas as pd
from src.analysis.v8_backtest import (
    _rsi, _max_dd, _by_year, _UNI,
    MAX_POS, STOP, TRAIL_TRIGGER, TRAIL_GIVEBACK, COST, MOM_DAYS,
)
from src.trader.alpaca_trader import get_client

SIM_START = "2023-01-01"
syms = sorted(set(_UNI.read_text().split()))
fetch_start = (date.fromisoformat(SIM_START) - timedelta(days=120)).isoformat()
print(f"取数(收盘+成交量){len(syms)}+2 只 from {fetch_start} …")

client = get_client()
closes, vols = {}, {}
allsy = syms + ["SPY", "QQQ"]
for i in range(0, len(allsy), 50):
    chunk = allsy[i:i + 50]
    try:
        df = client.get_bars(chunk, "1Day", start=fetch_start, adjustment="all", feed="iex").df
    except Exception:
        continue
    if df is None or len(df) == 0:
        continue
    if "symbol" in df.columns:
        for s, g in df.groupby("symbol"):
            closes[s] = g["close"]; vols[s] = g["volume"]
    elif isinstance(df.index, pd.MultiIndex):
        for s in df.index.get_level_values(0).unique():
            closes[s] = df.loc[s]["close"]; vols[s] = df.loc[s]["volume"]
    else:
        closes[chunk[0]] = df["close"]; vols[chunk[0]] = df["volume"]

px = pd.DataFrame(closes); px.index = pd.to_datetime(px.index).tz_localize(None); px = px.sort_index()
vol = pd.DataFrame(vols).reindex(columns=px.columns); vol.index = px.index
ma20 = px.rolling(20).mean(); ma50 = px.rolling(50).mean()
slope = ma50 - ma50.shift(5); mom = px / px.shift(MOM_DAYS) - 1
rsi = px.apply(_rsi); rvol = vol / vol.rolling(20).mean()
A = {k: v.values for k, v in {"px": px, "ma20": ma20, "ma50": ma50, "slope": slope,
                              "mom": mom, "rsi": rsi, "rvol": rvol}.items()}
cols = list(px.columns)
bench_cols = {cols.index("SPY")} | ({cols.index("QQQ")} if "QQQ" in cols else set())
sim_idx = [j for j, d in enumerate(px.index) if d >= pd.Timestamp(SIM_START)]
sim_dates = px.index[sim_idx]
print(f"模拟 {len(sim_idx)} 天 ({str(sim_dates[0])[:10]} ~ {str(sim_dates[-1])[:10]})\n")


def run(mode, vol_thr=None):
    cash, each = 1.0, 1.0 / MAX_POS
    pos = {}; eq = np.empty(len(sim_idx)); n_sells = 0
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
            rv = A["rvol"][i, ci]
            ma20_hit = False
            if not np.isnan(m20):
                if mode == "confirm2":       ma20_hit = below >= 2
                elif mode == "confirm2_vol": ma20_hit = below >= 2 and (not np.isnan(rv) and rv >= vol_thr)
                elif mode == "confirm1_vol": ma20_hit = (p < m20) and (not np.isnan(rv) and rv >= vol_thr)
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
    ("连续2根(现行)",          "confirm2",     None),
    ("连续2根 + 放量≥1.2",      "confirm2_vol", 1.2),
    ("连续2根 + 放量≥1.5",      "confirm2_vol", 1.5),
    ("单根 + 放量≥1.5",         "confirm1_vol", 1.5),
    ("单根 + 放量≥2.0",         "confirm1_vol", 2.0),
]
print(f"{'变体':<22}{'总收益':>9}{'最大回撤':>10}{'卖出次数':>9}  分年")
print("-" * 80)
for label, mode, vt in VARIANTS:
    eq, n = run(mode, vt)
    by = _by_year(sim_dates, eq)
    byr = " ".join(f"{y}:{v:+.0f}%" for y, v in by.items())
    print(f"{label:<22}{(eq[-1]-1)*100:>+8.0f}%{_max_dd(eq)*100:>9.0f}%{n:>9}  {byr}")
print("-" * 80)
for nm in ["SPY", "QQQ"]:
    tr, dd, by = bench(nm)
    print(f"{nm:<22}{tr:>+8.0f}%{dd:>9.0f}%{'—':>9}  " + " ".join(f"{y}:{v:+.0f}%" for y, v in by.items()))
