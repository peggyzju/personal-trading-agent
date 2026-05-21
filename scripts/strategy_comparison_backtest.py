"""
Strategy Comparison Backtest
============================
Compares three versions of the entry strategy to isolate the impact of:
  1. RSI threshold tightening (65 → 60)
  2. Signal gate change (candle_quality > 0 only → candle_quality ≥ 0)

Since historical ai_scores are unavailable, candle_quality proxies signal:
  candle_quality > 0  ≈ old "signal ∈ BUY/STRONG_BUY" gate
  candle_quality ≥ 0  ≈ new "signal != SELL, Rex rules decide" gate

Versions compared
-----------------
  V1 (old)  : RSI < 65  + candle_quality > 0
  V2 (mid)  : RSI < 60  + candle_quality > 0   (RSI only change)
  V3 (new)  : RSI < 60  + candle_quality ≥ 0   (both changes)

Trade simulation
----------------
  Entry  : next-day open + 0.2% slippage
  Stop   : ATR-based (1.5×ATR below entry), clamped 3–8%
  Target : 8% above entry
  Max hold: 10 days

Usage
-----
  cd /Users/tingaling97/personal-trading-agent
  python scripts/strategy_comparison_backtest.py [--months 6]
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import yfinance as yf

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from src.monitor.sp500_scanner import compute_kline_patterns


UNIVERSE = [
    "AAPL", "MSFT", "NVDA", "AMZN", "META", "GOOGL", "TSLA", "AVGO",
    "AMD", "QCOM", "ADBE", "CRM", "INTU", "SNOW", "DDOG", "CRWD", "PANW",
    "JPM", "GS", "V", "MA", "BAC", "AXP",
    "COST", "MCD", "NKE", "SBUX", "HD", "LOW",
    "UNH", "LLY", "JNJ", "ABBV", "MRK", "TMO",
    "XOM", "CVX",
    "CAT", "HON", "RTX", "UPS",
    "APP", "ARM", "MSTR", "HOOD", "AFRM", "UPST", "SOFI",
    "MOD", "KTOS", "RKLB", "IONQ",
]

STOP_PCT    = 5.0
TARGET_PCT  = 8.0
HOLD_DAYS   = 10
SLIPPAGE    = 0.002


def _scalar(val):
    if isinstance(val, pd.Series):
        return float(val.iloc[0])
    return float(val)


def _compute_rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain  = delta.clip(lower=0).rolling(period).mean()
    loss  = (-delta.clip(upper=0)).rolling(period).mean()
    rs    = gain / loss.replace(0, np.nan)
    return 100 - 100 / (1 + rs)


def _fetch(sym: str, start: str, end: str) -> pd.DataFrame | None:
    try:
        df = yf.download(sym, start=start, end=end, auto_adjust=True, progress=False)
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        df = df.loc[:, ~df.columns.duplicated()]
        for col in ("Open", "High", "Low", "Close", "Volume"):
            if col in df.columns and isinstance(df[col], pd.DataFrame):
                df[col] = df[col].iloc[:, 0]
        return df if not df.empty and len(df) >= 30 else None
    except Exception:
        return None


def _signals(df_up_to: pd.DataFrame, rsi_max: float, min_candle: int, require_bull: bool = True) -> dict | None:
    """
    Compute entry signals with configurable thresholds.
    rsi_max      : RSI upper bound (75 for 5/19 baseline, 65 for V1, 60 for V2/V3)
    min_candle   : minimum candle_quality (-2 = no filter, 0 = ≥0, 1 = >0)
    require_bull : False for 5/19 baseline (no today_bull check in original scanner)
    """
    if len(df_up_to) < 25:
        return None

    closes  = df_up_to["Close"].dropna()
    volumes = df_up_to["Volume"].dropna()
    if isinstance(closes, pd.DataFrame):
        closes = closes.iloc[:, 0]
    if isinstance(volumes, pd.DataFrame):
        volumes = volumes.iloc[:, 0]

    price = _scalar(closes.iloc[-1])
    rsi   = _scalar(_compute_rsi(closes).iloc[-1])
    if np.isnan(rsi):
        return None

    ma20 = _scalar(closes.rolling(20).mean().iloc[-1])
    if np.isnan(ma20) or ma20 == 0:
        return None
    vs_ma20 = (price - ma20) / ma20 * 100

    vol_prev    = _scalar(volumes.iloc[-2]) if len(volumes) >= 2 else _scalar(volumes.iloc[-1])
    vol_avg_raw = volumes.iloc[-22:-2].mean() if len(volumes) >= 22 else volumes.iloc[:-1].mean()
    vol_avg     = float(vol_avg_raw) if isinstance(vol_avg_raw, float) else _scalar(vol_avg_raw)
    vol_ratio   = vol_prev / vol_avg if vol_avg > 0 else 1.0

    # ATR for structured stop
    prev_c = closes.shift(1)
    hi = df_up_to["High"].dropna()
    lo = df_up_to["Low"].dropna()
    if isinstance(hi, pd.DataFrame): hi = hi.iloc[:, 0]
    if isinstance(lo, pd.DataFrame): lo = lo.iloc[:, 0]
    tr = pd.concat([hi - lo, (hi - prev_c).abs(), (lo - prev_c).abs()], axis=1).max(axis=1)
    atr = float(tr.rolling(14).mean().iloc[-1])

    # Entry filters
    if not (35 <= rsi <= rsi_max):
        return None
    if vs_ma20 < -10 or vs_ma20 > 8:
        return None
    if vol_ratio < 0.5:
        return None

    kline = compute_kline_patterns(df_up_to)
    if require_bull and not kline.get("today_bull", False):
        return None

    cq = kline.get("candle_quality", 0)
    if cq < min_candle:
        return None

    return {
        "rsi": round(rsi, 1),
        "vs_ma20": round(vs_ma20, 1),
        "vol_ratio": round(vol_ratio, 2),
        "candle_quality": cq,
        "patterns": kline.get("patterns", []),
        "price": round(price, 2),
        "atr": round(atr, 4),
        "ma20": round(ma20, 2),
    }


def _simulate(df_full: pd.DataFrame, entry_idx: int) -> dict:
    if entry_idx + 1 >= len(df_full):
        return {"pnl_pct": None, "exit_reason": "no_data", "days_held": 0}

    entry_open = _scalar(df_full["Open"].iloc[entry_idx + 1])
    ep = entry_open * (1 + SLIPPAGE)

    # ATR-based stop (mirror position_sizer.compute_structured_stop)
    closes  = df_full["Close"].iloc[:entry_idx + 1].dropna()
    hi_s    = df_full["High"].iloc[:entry_idx + 1].dropna()
    lo_s    = df_full["Low"].iloc[:entry_idx + 1].dropna()
    if isinstance(closes, pd.DataFrame): closes = closes.iloc[:, 0]
    if isinstance(hi_s, pd.DataFrame):   hi_s   = hi_s.iloc[:, 0]
    if isinstance(lo_s, pd.DataFrame):   lo_s   = lo_s.iloc[:, 0]
    prev_c = closes.shift(1)
    tr = pd.concat([hi_s - lo_s, (hi_s - prev_c).abs(), (lo_s - prev_c).abs()], axis=1).max(axis=1)
    atr = float(tr.rolling(14).mean().iloc[-1]) if len(tr) >= 14 else ep * 0.02
    ma20 = float(closes.rolling(20).mean().iloc[-1]) if len(closes) >= 20 else ep * 0.98

    raw_stop = max(ma20 * 0.99, ep - 1.5 * atr)
    floor    = ep * (1 - 0.08)
    ceiling  = ep * (1 - 0.03)
    stop_lvl = max(floor, min(raw_stop, ceiling))
    tgt_lvl  = ep * (1 + TARGET_PCT / 100)

    future = df_full.iloc[entry_idx + 1 : entry_idx + 1 + HOLD_DAYS]
    for i, (_, row) in enumerate(future.iterrows()):
        lo = _scalar(row["Low"])
        hi = _scalar(row["High"])
        if lo <= stop_lvl:
            exit_p = stop_lvl * (1 - SLIPPAGE)
            return {"pnl_pct": round((exit_p - ep) / ep * 100, 2),
                    "exit_reason": "stop", "days_held": i + 1}
        if hi >= tgt_lvl:
            exit_p = tgt_lvl * (1 - SLIPPAGE)
            return {"pnl_pct": round((exit_p - ep) / ep * 100, 2),
                    "exit_reason": "target", "days_held": i + 1}

    if len(future) == 0:
        return {"pnl_pct": None, "exit_reason": "no_data", "days_held": 0}
    last_close = _scalar(future.iloc[-1]["Close"])
    exit_p = last_close * (1 - SLIPPAGE)
    return {"pnl_pct": round((exit_p - ep) / ep * 100, 2),
            "exit_reason": "time", "days_held": len(future)}


def _run_version(dfs: dict[str, pd.DataFrame], rsi_max: float, min_candle: int, require_bull: bool = True) -> list[dict]:
    trades = []
    for sym, df in dfs.items():
        last_entry = -999
        for i in range(30, len(df) - 1):
            if i - last_entry < 5:
                continue
            sig = _signals(df.iloc[:i + 1], rsi_max, min_candle, require_bull)
            if sig is None:
                continue
            sim = _simulate(df, i)
            if sim["pnl_pct"] is None:
                continue
            trades.append({
                "symbol": sym,
                "date": str(df.index[i].date()),
                "rsi": sig["rsi"],
                "vs_ma20": sig["vs_ma20"],
                "candle_quality": sig["candle_quality"],
                "patterns": sig["patterns"],
                "pnl_pct": sim["pnl_pct"],
                "exit_reason": sim["exit_reason"],
                "days_held": sim["days_held"],
            })
            last_entry = i
    return trades


def _stats(trades: list[dict], label: str) -> dict:
    if not trades:
        return {"label": label, "n": 0}
    pnls = [t["pnl_pct"] for t in trades]
    wins = [p for p in pnls if p > 0]
    loss = [p for p in pnls if p <= 0]
    win_rate   = len(wins) / len(pnls) * 100
    avg_win    = np.mean(wins) if wins else 0
    avg_loss   = np.mean(loss) if loss else 0
    exp_value  = win_rate / 100 * avg_win + (1 - win_rate / 100) * avg_loss
    pf = abs(sum(wins) / sum(loss)) if sum(loss) != 0 else float("inf")
    exits = {}
    for t in trades:
        exits[t["exit_reason"]] = exits.get(t["exit_reason"], 0) + 1
    return {
        "label":      label,
        "n":          len(pnls),
        "win_rate":   round(win_rate, 1),
        "avg_win":    round(avg_win, 2),
        "avg_loss":   round(avg_loss, 2),
        "exp_value":  round(exp_value, 2),
        "profit_factor": round(pf, 2),
        "exits":      exits,
    }


def _candle_breakdown(trades: list[dict]) -> None:
    from collections import defaultdict
    by_cq = defaultdict(list)
    for t in trades:
        by_cq[t["candle_quality"]].append(t["pnl_pct"])
    print(f"  {'CQ':>3}  {'N':>5}  {'WR%':>6}  {'AvgRet':>7}")
    for cq in sorted(by_cq.keys()):
        pnls = by_cq[cq]
        wr   = sum(1 for p in pnls if p > 0) / len(pnls) * 100
        avg  = np.mean(pnls)
        print(f"  {cq:>3}  {len(pnls):>5}  {wr:>5.1f}%  {avg:>+6.2f}%")


def _rsi_breakdown(trades: list[dict]) -> None:
    buckets = [(35, 42), (42, 50), (50, 55), (55, 60), (60, 65)]
    print(f"  {'RSI bucket':>12}  {'N':>5}  {'WR%':>6}  {'AvgRet':>7}")
    for lo, hi in buckets:
        sub = [t["pnl_pct"] for t in trades if lo <= t["rsi"] < hi]
        if not sub:
            continue
        wr  = sum(1 for p in sub if p > 0) / len(sub) * 100
        avg = np.mean(sub)
        print(f"  {lo}-{hi:>2}          {len(sub):>5}  {wr:>5.1f}%  {avg:>+6.2f}%")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--months", type=int, default=6)
    args = parser.parse_args()

    end   = datetime.today()
    start = end - timedelta(days=args.months * 31)
    start_str = start.strftime("%Y-%m-%d")
    end_str   = end.strftime("%Y-%m-%d")

    print(f"\n{'='*60}")
    print(f"Strategy Comparison Backtest ({args.months}m: {start_str} → {end_str})")
    print(f"Universe: {len(UNIVERSE)} symbols | stop={STOP_PCT}% target={TARGET_PCT}% hold≤{HOLD_DAYS}d")
    print(f"{'='*60}\n")

    print(f"Downloading {len(UNIVERSE)} symbols…")
    dfs = {}
    for sym in UNIVERSE:
        df = _fetch(sym, start_str, end_str)
        if df is not None:
            dfs[sym] = df
    print(f"  → {len(dfs)}/{len(UNIVERSE)} loaded\n")

    versions = [
        # label, rsi_max, min_candle, require_bull
        ("V0 5/19基准 (RSI<75, 无K线过滤)", 75.0, -2, False),
        ("V1 旧版    (RSI<65, candle>0)",   65.0,  1, True),
        ("V2 中间版  (RSI<60, candle>0)",   60.0,  1, True),
        ("V3 今日    (RSI<60, candle≥0)",   60.0,  0, True),
    ]

    all_stats = []
    all_trades = {}

    for label, rsi_max, min_candle, require_bull in versions:
        print(f"Running {label}…")
        trades = _run_version(dfs, rsi_max, min_candle, require_bull)
        s = _stats(trades, label)
        all_stats.append(s)
        all_trades[label] = trades
        print(f"  完成：{s['n']} 笔交易\n")

    # ── Summary table ──────────────────────────────────────────────────────────
    print(f"\n{'='*60}")
    print("结果对比")
    print(f"{'='*60}")
    print(f"{'版本':<26} {'笔数':>5} {'胜率':>6} {'均盈':>7} {'均亏':>7} {'期望值':>7} {'盈亏比':>6}")
    print("-" * 62)
    for s in all_stats:
        if s["n"] == 0:
            print(f"{s['label']:<26}     0   —      —       —       —       —")
            continue
        exits = s.get("exits", {})
        print(
            f"{s['label']:<26} {s['n']:>5} {s['win_rate']:>5.1f}% "
            f"{s['avg_win']:>+6.2f}% {s['avg_loss']:>+6.2f}% "
            f"{s['exp_value']:>+6.2f}% {s['profit_factor']:>6.2f}x"
        )
        print(f"  离场: stop={exits.get('stop',0)} target={exits.get('target',0)} time={exits.get('time',0)}")

    # ── Candle quality breakdown for each version ──────────────────────────────
    for label, _, _, _ in versions:
        trades = all_trades[label]
        if not trades:
            continue
        print(f"\n{label} — K线质量分布:")
        _candle_breakdown(trades)

    # ── RSI bucket breakdown for each version ─────────────────────────────────
    for label, _, _, _ in versions:
        trades = all_trades[label]
        if not trades:
            continue
        print(f"\n{label} — RSI 区间分布:")
        _rsi_breakdown(trades)

    # ── Delta analysis ─────────────────────────────────────────────────────────
    if len(all_stats) == 4 and all(s["n"] > 0 for s in all_stats):
        v0, v1, v2, v3 = all_stats
        print(f"\n{'='*60}")
        print("Delta 分析（5/19基准 → 今日策略）")
        print(f"{'='*60}")
        def _delta_row(label, a, b):
            dn = b['n'] - a['n']
            de = b['exp_value'] - a['exp_value']
            print(f"{label}: 笔数 {a['n']} → {b['n']} ({dn:+d}), "
                  f"期望值 {a['exp_value']:+.2f}% → {b['exp_value']:+.2f}% ({de:+.2f}%)")
        _delta_row("加 today_bull + MA20过滤 (V0→V1)", v0, v1)
        _delta_row("RSI 收紧 65→60          (V1→V2)", v1, v2)
        _delta_row("信号门 candle>0→≥0      (V2→V3)", v2, v3)
        _delta_row("总变化 5/19→今日         (V0→V3)", v0, v3)


if __name__ == "__main__":
    main()
