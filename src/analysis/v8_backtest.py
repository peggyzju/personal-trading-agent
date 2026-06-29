"""v8 趋势打法 组合级回测(后端版)— 供复盘页「回测」用,替代测不了 v8 的旧版本对比。

确定性规则(无 AI、无后见之明),数据走 Alpaca(feed=iex,不用 yfinance)。
逻辑与 scripts/v8_robustness.py 一致:趋势门(MA50上+MA50升+RSI50-80+3月动量>0+不过高)
→ 按动量排名买 top N → 退出(-8%止损 / 追踪+6%激活-8%回撤 / 跌破MA20)。对照同期 SPY。
返回结构化结果给前端(分年收益 + 总收益 + 最大回撤)。
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

_UNI = Path(__file__).resolve().parent.parent.parent / "data" / "sp500_constituents.txt"

MAX_POS = 10
STOP = 0.08
TRAIL_TRIGGER = 0.06
TRAIL_GIVEBACK = 0.08
COST = 0.0005
MOM_DAYS = 60


def _slice_bounds(period: str, sim_dates) -> tuple:
    """周期 → (lo, hi) Timestamp。在'永远连续跑 2023→今天'的曲线上切出'总收益'区间,
    所以分年永远 canonical(2026 在任何周期下都是同一个数),周期只决定头部总收益看哪一段。"""
    lo0, hi0 = sim_dates[0], sim_dates[-1]
    if period in ("2023", "2024", "2025", "2026"):
        y = int(period)
        return pd.Timestamp(f"{y}-01-01"), pd.Timestamp(f"{y}-12-31")
    if period == "1y":
        return hi0 - pd.Timedelta(days=365), hi0
    if period == "3y":
        return lo0, hi0
    return hi0 - pd.Timedelta(days=183), hi0   # 6mo 默认


def _fetch(symbols, start):
    from src.trader.alpaca_trader import get_client
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
            for s, g in df.groupby("symbol"):
                closes[s] = g["close"]
        elif isinstance(df.index, pd.MultiIndex):
            for s in df.index.get_level_values(0).unique():
                closes[s] = df.loc[s]["close"]
        else:
            closes[chunk[0]] = df["close"]
    px = pd.DataFrame(closes)
    px.index = pd.to_datetime(px.index).tz_localize(None)
    return px.sort_index()


def _rsi(s, n=14):
    d = s.diff()
    up = d.clip(lower=0).ewm(alpha=1/n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1/n, adjust=False).mean()
    return 100 - 100 / (1 + up / dn.replace(0, 1e-9))


def _max_dd(eq):
    peak = np.maximum.accumulate(eq)
    return float(((eq - peak) / peak).min()) if len(eq) else 0.0


def _by_year(idx, eq):
    out = {}
    for y in sorted({d.year for d in idx}):
        mask = [d.year == y for d in idx]
        yr = eq[mask]
        if len(yr) > 1:
            out[str(y)] = round((yr[-1] / yr[0] - 1) * 100, 1)
    return out


def run_v8_backtest(period: str = "6mo") -> dict:
    if not _UNI.exists():
        return {"status": "error", "error": "缺 sp500_constituents.txt"}
    symbols = sorted(set(_UNI.read_text().split()))
    # 永远连续从 2023 跑到今天 → 分年 canonical;周期只决定头部"总收益"看哪一段(切片)
    sim_start, sim_end = "2023-01-01", date.today().isoformat()
    fetch_start = (date.fromisoformat(sim_start) - timedelta(days=120)).isoformat()  # MA50/动量预热

    px = _fetch(symbols + ["SPY", "QQQ"], fetch_start)
    if px.empty or "SPY" not in px.columns:
        return {"status": "error", "error": "取数失败"}
    px = px[px.index <= pd.Timestamp(sim_end)]

    ma20 = px.rolling(20).mean(); ma50 = px.rolling(50).mean()
    slope = ma50 - ma50.shift(5); mom = px / px.shift(MOM_DAYS) - 1
    rsi = px.apply(_rsi)
    A = {k: v.values for k, v in {"px": px, "ma20": ma20, "ma50": ma50, "slope": slope, "mom": mom, "rsi": rsi}.items()}
    cols = list(px.columns)
    spy_col = cols.index("SPY")
    qqq_col = cols.index("QQQ") if "QQQ" in cols else -1
    bench_cols = {spy_col, qqq_col} - {-1}

    sim_idx = [j for j, d in enumerate(px.index) if d >= pd.Timestamp(sim_start)]
    if len(sim_idx) < 5:
        return {"status": "error", "error": "样本不足"}

    cash, each = 1.0, 1.0 / MAX_POS
    pos = {}
    eq = np.empty(len(sim_idx))
    for k, i in enumerate(sim_idx):
        row = A["px"][i]
        for ci in list(pos):
            p = row[ci]
            if np.isnan(p):
                continue
            ent, frac, hi = pos[ci]
            hi = max(hi, p); pos[ci][2] = hi
            gain = p / ent - 1; m20 = A["ma20"][i, ci]
            if gain <= -STOP or (gain >= TRAIL_TRIGGER and p <= hi * (1 - TRAIL_GIVEBACK)) \
               or (not np.isnan(m20) and p < m20):
                cash += frac * (p / ent) * (1 - COST); del pos[ci]
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
                    spend = min(each, cash)
                    pos[ci] = [row[ci], spend * (1 - COST), row[ci]]; cash -= spend
        val = cash
        for ci, (ent, frac, hi) in pos.items():
            p = row[ci]
            if not np.isnan(p):
                val += frac * (p / ent)
        eq[k] = val

    sim_dates = px.index[sim_idx]

    def _bench_eq(sym_name):
        if sym_name not in px.columns:
            return None
        s = px[sym_name].values[sim_idx]
        if len(s) < 2 or s[0] == 0 or np.isnan(s[0]):
            return None
        return s / s[0]

    spy_eq, qqq_eq = _bench_eq("SPY"), _bench_eq("QQQ")

    # 周期切片:总收益/回撤只看选定区间;分年永远用整条连续曲线(canonical)
    lo, hi = _slice_bounds(period, sim_dates)
    sl = [k for k, d in enumerate(sim_dates) if lo <= d <= hi]
    if len(sl) < 2:
        sl = list(range(len(sim_dates)))
    s0, s1 = sl[0], sl[-1]

    def _side(eq_arr):
        if eq_arr is None:
            return None
        seg = eq_arr[s0:s1 + 1]
        return {
            "total_return_pct": round((seg[-1] / seg[0] - 1) * 100, 1),
            "max_drawdown_pct": round(_max_dd(seg) * 100, 1),
            "by_year": _by_year(sim_dates, eq_arr),   # 整条曲线 → 分年 canonical
        }

    return {
        "status": "done",
        "period": period,
        "date_range": f"{str(sim_dates[s0])[:10]} ~ {str(sim_dates[s1])[:10]}",
        "n_months": round((s1 - s0 + 1) / 21, 1),
        "v8": _side(eq),
        "spy": _side(spy_eq),
        "qqq": _side(qqq_eq),
        "generated_at": datetime.utcnow().isoformat(),
    }
