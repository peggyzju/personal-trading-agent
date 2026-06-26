#!/usr/bin/env python3
"""横截面动量 组合级回测 vs SPY —— 回答"换时间尺度(天→月)选股有没有救"。

逻辑(经典 cross-sectional momentum):
  每月末,对全股票池按"过去 LOOKBACK 个月总收益"排名,
  买排名最高的 TOP_N 只,等权,持有 1 个月,月末再平衡。
  无前瞻:排名只用到再平衡日及之前的数据,收益在之后 1 个月才实现。
  扣保守换手成本(每次再平衡每只 0.05%)。

对照:同期 SPY 买入持有。跨 2023/2024/2025/2026YTD 分年看 总收益/最大回撤。
现系统实盘基准(2026-05~06,8 周):−2.79% vs SPY +1.76%。

用法:python3 scripts/xsec_momentum_backtest.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from dotenv import load_dotenv
load_dotenv()

import pandas as pd
from src.monitor.sp500_scanner import SECTOR_MAP
from src.trader.alpaca_trader import get_client

START = "2022-07-01"          # 需在首个再平衡(2023-01)前留 6 个月 lookback
REBAL_START = "2023-01-01"
COST_PER_REBAL = 0.0005       # 单边 0.05% 换手成本(保守)


def fetch_monthly_closes(symbols, start):
    """取日线 → 月末收盘价面板。DataFrame: index=月末, cols=symbol。"""
    client = get_client()
    frames = {}
    BATCH = 50
    for i in range(0, len(symbols), BATCH):
        chunk = symbols[i:i + BATCH]
        try:
            df = client.get_bars(chunk, "1Day", start=start, adjustment="all").df
        except Exception as e:
            print(f"  [warn] batch {chunk[:3]}… 取数失败: {e}")
            continue
        if df is None or len(df) == 0:
            continue
        # multi-index (symbol, timestamp) 或单 symbol
        if "symbol" in df.columns:
            for sym, g in df.groupby("symbol"):
                frames[sym] = g["close"]
        elif isinstance(df.index, pd.MultiIndex):
            for sym in df.index.get_level_values(0).unique():
                frames[sym] = df.loc[sym]["close"]
        else:  # 单 symbol
            frames[chunk[0]] = df["close"]
    if not frames:
        return pd.DataFrame()
    px = pd.DataFrame(frames)
    px.index = pd.to_datetime(px.index).tz_localize(None)
    monthly = px.resample("ME").last()
    return monthly


def max_drawdown(equity):
    peak = equity.cummax()
    return ((equity - peak) / peak).min()


def run_strategy(monthly, lookback, top_n):
    """返回每月组合收益 Series(index=实现收益的那个月)。"""
    rets = monthly.pct_change()
    months = monthly.index
    out = {}
    for i in range(lookback, len(months) - 1):
        t = months[i]
        if t < pd.Timestamp(REBAL_START):
            continue
        # 排名:t 时点过去 lookback 个月收益
        mom = monthly.iloc[i] / monthly.iloc[i - lookback] - 1
        mom = mom.dropna()
        if len(mom) < top_n:
            continue
        winners = mom.sort_values(ascending=False).head(top_n).index
        # 实现收益:t → t+1
        nxt = rets.iloc[i + 1][winners].dropna()
        if len(nxt) == 0:
            continue
        port_ret = nxt.mean() - COST_PER_REBAL  # 等权 + 换手成本
        out[months[i + 1]] = port_ret
    return pd.Series(out)


def summarize(name, monthly_ret):
    eq = (1 + monthly_ret).cumprod()
    by_year = {}
    for y in sorted({d.year for d in monthly_ret.index}):
        yr = monthly_ret[[d.year == y for d in monthly_ret.index]]
        by_year[y] = (1 + yr).prod() - 1
    total = eq.iloc[-1] - 1 if len(eq) else 0
    mdd = max_drawdown(eq) if len(eq) else 0
    n = len(monthly_ret)
    cagr = (1 + total) ** (12 / n) - 1 if n else 0
    print(f"\n[{name}]  {n} 个月")
    for y, r in by_year.items():
        print(f"    {y}: {r*100:+6.1f}%")
    print(f"    总收益 {total*100:+.1f}%  |  年化 {cagr*100:+.1f}%  |  最大回撤 {mdd*100:.1f}%")
    return total, mdd


def main():
    uni_file = Path(__file__).resolve().parent.parent / "data" / "sp500_constituents.txt"
    if uni_file.exists():
        symbols = sorted(set(uni_file.read_text().split()))
        src = f"S&P500 名单({uni_file.name})"
    else:
        symbols = sorted(SECTOR_MAP.keys())
        src = "SECTOR_MAP(偏科技)"
    print(f"股票池: {len(symbols)} 只 [{src}] | 起始 {START} | 再平衡起点 {REBAL_START}")
    print("取数中(日线→月末)…")
    monthly = fetch_monthly_closes(symbols + ["SPY"], START)
    if monthly.empty:
        print("❌ 取数失败"); return
    cov = monthly.columns.tolist()
    print(f"成功取到 {len(cov)} 只 (含 SPY={'SPY' in cov})")

    # SPY 基准
    if "SPY" in monthly.columns:
        spy_ret = monthly["SPY"].pct_change().dropna()
        spy_ret = spy_ret[spy_ret.index >= pd.Timestamp(REBAL_START)]
        summarize("SPY 买入持有", spy_ret)

    # 动量策略:几组参数看稳健性
    universe = monthly.drop(columns=["SPY"], errors="ignore")
    for lookback in (3, 6, 12):
        for top_n in (10, 20):
            r = run_strategy(universe, lookback, top_n)
            if len(r):
                summarize(f"横截面动量 lookback={lookback}月 topN={top_n}", r)

    print("\n判读:某组动量参数 跨年总收益>SPY 且 最大回撤不更差 → '换到月级动量'有救;")
    print("      全面跑输 SPY → 选股层(技术类)整体没救,应转被动/趋势择时。")


if __name__ == "__main__":
    main()
