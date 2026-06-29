#!/usr/bin/env python3
"""v8 趋势统一打法 — 组合级回测 vs SPY(确定性规则,无 AI,无后见之明污染)。

thesis:买强势、顺势、拿住赢家。验证"统一到趋势"是否比躺平(SPY)好。

选股(每日):上升趋势 + 强动量
  - price > MA50 且 MA50 上升(5日斜率>0)
  - RSI 50-80(强势区,不超卖不过热)
  - 60日动量 > 0
  - 不过度延伸:price ≤ MA20 × 1.15
  - 候选按 60日动量排名,买最强的填满空位(等权,最多 N 仓)

持有/退出(让赢家跑):
  - 初始止损:入场 × (1 - STOP)
  - 追踪止盈:浮盈 ≥ +6% 激活后,从高水位回撤 ≥ 8% 退出(固定,用户选)
  - 趋势破位:收盘 < MA20 退出
  - 趋势没破就一直拿(无固定持有期)

对照:同期 SPY 买入持有,同一股票池(幸存者偏差对两者一致 → 相对比较可信)。
用法:python3 scripts/v8_trend_backtest.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from dotenv import load_dotenv
load_dotenv()

import pandas as pd
from src.trader.alpaca_trader import get_client

START = "2022-09-01"        # 留 ~4 个月给 MA50/动量预热
SIM_START = "2023-01-01"
MAX_POS = 10
STOP = 0.08                 # 初始止损 -8%
TRAIL_TRIGGER = 0.06        # 浮盈 +6% 激活追踪
TRAIL_GIVEBACK = 0.08       # 高水位回撤 8% 退出(固定)
COST = 0.0005               # 单边换手成本


def fetch_closes_hilo(symbols, start):
    client = get_client()
    closes, highs = {}, {}
    for i in range(0, len(symbols), 50):
        chunk = symbols[i:i + 50]
        try:
            df = client.get_bars(chunk, "1Day", start=start, adjustment="all", feed="iex").df
        except Exception as e:
            print(f"  [warn] {chunk[:2]}… 取数失败: {e}")
            continue
        if df is None or len(df) == 0:
            continue
        if "symbol" in df.columns:
            for sym, g in df.groupby("symbol"):
                closes[sym] = g["close"]; highs[sym] = g["high"]
        elif isinstance(df.index, pd.MultiIndex):
            for sym in df.index.get_level_values(0).unique():
                closes[sym] = df.loc[sym]["close"]; highs[sym] = df.loc[sym]["high"]
        else:
            closes[chunk[0]] = df["close"]; highs[chunk[0]] = df["high"]
    px = pd.DataFrame(closes); px.index = pd.to_datetime(px.index).tz_localize(None)
    return px.sort_index()


def rsi(series, n=14):
    d = series.diff()
    up = d.clip(lower=0).ewm(alpha=1/n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1/n, adjust=False).mean()
    rs = up / dn.replace(0, 1e-9)
    return 100 - 100 / (1 + rs)


def max_drawdown(eq):
    peak = eq.cummax()
    return ((eq - peak) / peak).min()


def main():
    uni_file = Path(__file__).resolve().parent.parent / "data" / "sp500_constituents.txt"
    symbols = sorted(set(uni_file.read_text().split())) if uni_file.exists() else []
    print(f"股票池 {len(symbols)} 只 | 预热起 {START} | 模拟起 {SIM_START} | 最多 {MAX_POS} 仓")
    print("取数中(日线 close+high)…")
    px = fetch_closes_hilo(symbols, START)
    print(f"取到 {px.shape[1]} 只 × {px.shape[0]} 天")

    # 指标面板(向量化,无前瞻:用到 t 当日及之前)
    ma20 = px.rolling(20).mean()
    ma50 = px.rolling(50).mean()
    ma50_slope = ma50 - ma50.shift(5)
    mom = px / px.shift(60) - 1
    rsi_p = px.apply(rsi)

    dates = px.index[px.index >= pd.Timestamp(SIM_START)]
    cash = 1.0
    n_start = MAX_POS
    each = 1.0 / n_start
    positions = {}     # sym -> {entry, shares_value_frac, hi}
    equity_curve = {}

    for t in dates:
        # 当日价格
        prices = px.loc[t]
        # 1) 更新持仓 + 退出
        for sym in list(positions.keys()):
            p = prices.get(sym)
            if p != p or p is None:    # NaN
                continue
            pos = positions[sym]
            pos["hi"] = max(pos["hi"], p)
            gain = p / pos["entry"] - 1
            exit_now = False
            if gain <= -STOP:
                exit_now = True
            elif gain >= TRAIL_TRIGGER and p <= pos["hi"] * (1 - TRAIL_GIVEBACK):
                exit_now = True
            else:
                m20 = ma20.loc[t, sym] if sym in ma20.columns else None
                if m20 == m20 and p < m20:
                    exit_now = True
            if exit_now:
                cash += pos["frac"] * (p / pos["entry"]) * (1 - COST)
                del positions[sym]

        # 2) 填空位:候选 = 上升趋势 + 强动量,按动量排名
        free = MAX_POS - len(positions)
        if free > 0 and cash > each * 0.5:
            cand = []
            for sym in px.columns:
                if sym in positions:
                    continue
                p = prices.get(sym)
                m50 = ma50.loc[t, sym]; m20 = ma20.loc[t, sym]
                sl = ma50_slope.loc[t, sym]; r = rsi_p.loc[t, sym]; mo = mom.loc[t, sym]
                if any(x != x for x in (p, m50, m20, sl, r, mo)):
                    continue
                if p > m50 and sl > 0 and 50 <= r <= 80 and mo > 0 and p <= m20 * 1.15:
                    cand.append((mo, sym, p))
            cand.sort(reverse=True)
            for mo, sym, p in cand[:free]:
                if cash < each * 0.5:
                    break
                spend = min(each, cash)
                positions[sym] = {"entry": p, "frac": spend * (1 - COST), "hi": p}
                cash -= spend

        # 3) 组合市值
        val = cash
        for sym, pos in positions.items():
            p = prices.get(sym)
            if p == p and p is not None:
                val += pos["frac"] * (p / pos["entry"])
        equity_curve[t] = val

    eq = pd.Series(equity_curve)
    # SPY 基准
    spy = fetch_closes_hilo(["SPY"], START)["SPY"]
    spy = spy[spy.index >= pd.Timestamp(SIM_START)]
    spy_ret = spy.iloc[-1] / spy.iloc[0] - 1

    def by_year(series_eq):
        out = {}
        for y in sorted({d.year for d in series_eq.index}):
            yr = series_eq[[d.year == y for d in series_eq.index]]
            if len(yr) > 1:
                out[y] = yr.iloc[-1] / yr.iloc[0] - 1
        return out

    print("\n===== v8 趋势打法 =====")
    for y, r in by_year(eq).items():
        print(f"    {y}: {r*100:+6.1f}%")
    print(f"    总收益 {(eq.iloc[-1]-1)*100:+.1f}%  |  最大回撤 {max_drawdown(eq)*100:.1f}%")
    print("\n===== SPY 买入持有(同期)=====")
    spy_eq = spy / spy.iloc[0]
    for y, r in by_year(spy_eq).items():
        print(f"    {y}: {r*100:+6.1f}%")
    print(f"    总收益 {spy_ret*100:+.1f}%  |  最大回撤 {max_drawdown(spy_eq)*100:.1f}%")
    print(f"\n判读:v8 总收益 > SPY 且 回撤不明显更差 → 趋势统一方向成立,可上线;")
    print(f"      跑输 SPY → 连确定性趋势规则都赢不了大盘,需重新考虑(转被动/趋势择时)。")


if __name__ == "__main__":
    main()
