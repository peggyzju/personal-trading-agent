"""
Strategy Comparison Backtest
============================
Compares entry strategy versions to isolate the impact of:
  1. RSI threshold tightening (65 → 60)
  2. candle_quality gate (≥0 → >0)
  3. Medium/long-term trend filter (new: vs_ma50 + momentum_3m rubric)

Versions compared
-----------------
  V2  (基准)   : RSI 35-60  + candle_quality > 0  (current production)
  V12 (去陷阱) : V2 but skip DOWNTREND_TRAP (vs_ma50<-8% AND mom_3m<-20%)
  V13 (上升趋势): V2 but only UPTREND setups (vs_ma50>0% AND mom_3m>-10%)

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


def _signals(
    df_up_to: pd.DataFrame,
    rsi_max: float,
    min_candle: int,
    require_bull: bool = True,
    confirm: str = "none",
    rsi_min: float = 35.0,
) -> dict | None:
    if len(df_up_to) < 25:
        return None

    closes  = df_up_to["Close"].dropna()
    volumes = df_up_to["Volume"].dropna()
    opens   = df_up_to["Open"].dropna()
    if isinstance(closes, pd.DataFrame):  closes  = closes.iloc[:, 0]
    if isinstance(volumes, pd.DataFrame): volumes = volumes.iloc[:, 0]
    if isinstance(opens, pd.DataFrame):   opens   = opens.iloc[:, 0]

    price = _scalar(closes.iloc[-1])
    rsi   = _scalar(_compute_rsi(closes).iloc[-1])
    if np.isnan(rsi):
        return None

    ma20 = _scalar(closes.rolling(20).mean().iloc[-1])
    if np.isnan(ma20) or ma20 == 0:
        return None
    vs_ma20 = (price - ma20) / ma20 * 100

    ma5 = _scalar(closes.rolling(5).mean().iloc[-1]) if len(closes) >= 5 else price

    vol_prev    = _scalar(volumes.iloc[-2]) if len(volumes) >= 2 else _scalar(volumes.iloc[-1])
    vol_avg_raw = volumes.iloc[-22:-2].mean() if len(volumes) >= 22 else volumes.iloc[:-1].mean()
    vol_avg     = float(vol_avg_raw) if isinstance(vol_avg_raw, float) else _scalar(vol_avg_raw)
    vol_ratio   = vol_prev / vol_avg if vol_avg > 0 else 1.0

    prev_c = closes.shift(1)
    hi = df_up_to["High"].dropna()
    lo = df_up_to["Low"].dropna()
    if isinstance(hi, pd.DataFrame): hi = hi.iloc[:, 0]
    if isinstance(lo, pd.DataFrame): lo = lo.iloc[:, 0]
    tr = pd.concat([hi - lo, (hi - prev_c).abs(), (lo - prev_c).abs()], axis=1).max(axis=1)
    atr = float(tr.rolling(14).mean().iloc[-1])

    # Entry filters
    if not (rsi_min <= rsi <= rsi_max):
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

    # Trend confirmation
    if confirm == "2bull":
        if len(closes) < 2 or len(opens) < 2:
            return None
        if not (_scalar(closes.iloc[-2]) > _scalar(opens.iloc[-2])):
            return None
    elif confirm == "above_ma5":
        if price <= ma5:
            return None
    elif confirm == "both":
        if len(closes) < 2 or len(opens) < 2:
            return None
        if not (_scalar(closes.iloc[-2]) > _scalar(opens.iloc[-2])) or price <= ma5:
            return None

    # Tech-quality proxy (0–9)
    tq  = (3 if 42 <= rsi <= 55 else 2 if 35 <= rsi < 42 else 1 if rsi <= 58 else 0)
    tq += (2 if -1 <= vs_ma20 <= 3 else 1 if vs_ma20 <= 5 else 0)
    tq += (2 if vol_ratio >= 1.5 else 1 if vol_ratio >= 1.0 else 0)
    tq += (2 if cq == 2 else 1 if cq == 1 else 0)

    # ── Medium/long-term trend tier (simulates new ai_score rubric) ───────────
    ma50_series = closes.rolling(50).mean()
    ma50 = _scalar(ma50_series.iloc[-1]) if len(closes) >= 50 and not np.isnan(_scalar(ma50_series.iloc[-1])) else None
    vs_ma50 = (price - ma50) / ma50 * 100 if ma50 else 0.0

    price_3m = float(closes.iloc[-63]) if len(closes) >= 63 else float(closes.iloc[0])
    momentum_3m = (price - price_3m) / price_3m * 100

    if vs_ma50 < -8 and momentum_3m < -20:
        trend_tier = "trap"        # DOWNTREND TRAP: cap ai_score at 4 → effectively skip
    elif vs_ma50 > 0 and momentum_3m > -10:
        trend_tier = "uptrend"     # healthy pullback within intact trend
    else:
        trend_tier = "neutral"     # recovering but structure uncertain

    return {
        "rsi":          round(rsi, 1),
        "vs_ma20":      round(vs_ma20, 1),
        "vs_ma50":      round(vs_ma50, 1),
        "momentum_3m":  round(momentum_3m, 1),
        "vol_ratio":    round(vol_ratio, 2),
        "candle_quality": cq,
        "tech_quality": tq,
        "trend_tier":   trend_tier,
        "patterns":     kline.get("patterns", []),
        "price":        round(price, 2),
        "atr":          round(atr, 4),
        "ma20":         round(ma20, 2),
    }


def _simulate(df_full: pd.DataFrame, entry_idx: int) -> dict:
    if entry_idx + 1 >= len(df_full):
        return {"pnl_pct": None, "exit_reason": "no_data", "days_held": 0}

    entry_open = _scalar(df_full["Open"].iloc[entry_idx + 1])
    ep = entry_open * (1 + SLIPPAGE)

    closes  = df_full["Close"].iloc[:entry_idx + 1].dropna()
    hi_s    = df_full["High"].iloc[:entry_idx + 1].dropna()
    lo_s    = df_full["Low"].iloc[:entry_idx + 1].dropna()
    if isinstance(closes, pd.DataFrame): closes = closes.iloc[:, 0]
    if isinstance(hi_s, pd.DataFrame):   hi_s   = hi_s.iloc[:, 0]
    if isinstance(lo_s, pd.DataFrame):   lo_s   = lo_s.iloc[:, 0]
    prev_c = closes.shift(1)
    tr = pd.concat([hi_s - lo_s, (hi_s - prev_c).abs(), (lo_s - prev_c).abs()], axis=1).max(axis=1)
    atr  = float(tr.rolling(14).mean().iloc[-1]) if len(tr) >= 14 else ep * 0.02
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


def _run_version(
    dfs: dict[str, pd.DataFrame],
    rsi_max: float,
    min_candle: int,
    require_bull: bool = True,
    min_tq: int = 0,
    confirm: str = "none",
    rsi_min: float = 35.0,
    tier_filter: str = "any",   # "any" | "no_trap" | "uptrend_only"
) -> list[dict]:
    trades = []
    for sym, df in dfs.items():
        last_entry = -999
        for i in range(65, len(df) - 1):   # start at 65 to allow 63-bar lookback
            if i - last_entry < 5:
                continue
            sig = _signals(df.iloc[:i + 1], rsi_max, min_candle, require_bull, confirm, rsi_min)
            if sig is None:
                continue
            if sig["tech_quality"] < min_tq:
                continue
            tier = sig["trend_tier"]
            if tier_filter == "no_trap" and tier == "trap":
                continue
            if tier_filter == "uptrend_only" and tier != "uptrend":
                continue
            sim = _simulate(df, i)
            if sim["pnl_pct"] is None:
                continue
            trades.append({
                "symbol":         sym,
                "date":           str(df.index[i].date()),
                "rsi":            sig["rsi"],
                "vs_ma20":        sig["vs_ma20"],
                "vs_ma50":        sig["vs_ma50"],
                "momentum_3m":    sig["momentum_3m"],
                "candle_quality": sig["candle_quality"],
                "tech_quality":   sig["tech_quality"],
                "trend_tier":     tier,
                "patterns":       sig["patterns"],
                "pnl_pct":        sim["pnl_pct"],
                "exit_reason":    sim["exit_reason"],
                "days_held":      sim["days_held"],
            })
            last_entry = i
    return trades


def _stats(trades: list[dict], label: str) -> dict:
    if not trades:
        return {"label": label, "n": 0}
    pnls = [t["pnl_pct"] for t in trades]
    wins = [p for p in pnls if p > 0]
    loss = [p for p in pnls if p <= 0]
    win_rate  = len(wins) / len(pnls) * 100
    avg_win   = np.mean(wins) if wins else 0
    avg_loss  = np.mean(loss) if loss else 0
    exp_value = win_rate / 100 * avg_win + (1 - win_rate / 100) * avg_loss
    pf = abs(sum(wins) / sum(loss)) if sum(loss) != 0 else float("inf")
    exits = {}
    for t in trades:
        exits[t["exit_reason"]] = exits.get(t["exit_reason"], 0) + 1
    return {
        "label":         label,
        "n":             len(pnls),
        "win_rate":      round(win_rate, 1),
        "avg_win":       round(avg_win, 2),
        "avg_loss":      round(avg_loss, 2),
        "exp_value":     round(exp_value, 2),
        "profit_factor": round(pf, 2),
        "exits":         exits,
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


def _tier_breakdown(trades: list[dict]) -> None:
    from collections import defaultdict
    by_tier = defaultdict(list)
    for t in trades:
        by_tier[t["trend_tier"]].append(t["pnl_pct"])
    print(f"  {'Tier':>12}  {'N':>5}  {'WR%':>6}  {'AvgRet':>7}")
    for tier in ("uptrend", "neutral", "trap"):
        pnls = by_tier.get(tier, [])
        if not pnls:
            continue
        wr  = sum(1 for p in pnls if p > 0) / len(pnls) * 100
        avg = np.mean(pnls)
        print(f"  {tier:>12}  {len(pnls):>5}  {wr:>5.1f}%  {avg:>+6.2f}%")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--months", type=int, default=6)
    args = parser.parse_args()

    end        = datetime.today()
    start      = end - timedelta(days=args.months * 31)
    # Fetch extra 90 days so MA50 and momentum_3m have real data on day 1
    data_start = start - timedelta(days=90)
    start_str  = start.strftime("%Y-%m-%d")
    end_str    = end.strftime("%Y-%m-%d")
    data_start_str = data_start.strftime("%Y-%m-%d")

    print(f"\n{'='*60}")
    print(f"Strategy Comparison Backtest ({args.months}m: {start_str} → {end_str})")
    print(f"Universe: {len(UNIVERSE)} symbols | stop={STOP_PCT}% target={TARGET_PCT}% hold≤{HOLD_DAYS}d")
    print(f"{'='*60}\n")

    print(f"Downloading {len(UNIVERSE)} symbols (data from {data_start_str})…")
    dfs = {}
    for sym in UNIVERSE:
        df = _fetch(sym, data_start_str, end_str)
        if df is not None:
            dfs[sym] = df
    print(f"  → {len(dfs)}/{len(UNIVERSE)} loaded\n")

    # label, rsi_max, min_candle, require_bull, min_tq, confirm, rsi_min, tier_filter
    versions = [
        ("V2  基准 (当前生产)",     60.0, 1, True, 0, "none", 35.0, "any"),
        ("V12 去掉下跌陷阱",        60.0, 1, True, 0, "none", 35.0, "no_trap"),
        ("V13 仅上升趋势回调",      60.0, 1, True, 0, "none", 35.0, "uptrend_only"),
    ]

    all_stats  = []
    all_trades = {}

    for label, rsi_max, min_candle, require_bull, min_tq, confirm, rsi_min, tier_filter in versions:
        print(f"Running {label}…")
        trades = _run_version(dfs, rsi_max, min_candle, require_bull, min_tq, confirm, rsi_min, tier_filter)
        s = _stats(trades, label)
        all_stats.append(s)
        all_trades[label] = trades
        print(f"  完成：{s['n']} 笔交易\n")

    # ── Summary table ──────────────────────────────────────────────────────────
    print(f"\n{'='*60}")
    print("结果对比")
    print(f"{'='*60}")
    print(f"{'版本':<28} {'笔数':>5} {'胜率':>6} {'均盈':>7} {'均亏':>7} {'期望值':>7} {'盈亏比':>6}")
    print("-" * 64)
    for s in all_stats:
        if s["n"] == 0:
            print(f"{s['label']:<28}     0   —      —       —       —       —")
            continue
        exits = s.get("exits", {})
        print(
            f"{s['label']:<28} {s['n']:>5} {s['win_rate']:>5.1f}% "
            f"{s['avg_win']:>+6.2f}% {s['avg_loss']:>+6.2f}% "
            f"{s['exp_value']:>+6.2f}% {s['profit_factor']:>6.2f}x"
        )
        print(f"  离场: stop={exits.get('stop',0)} target={exits.get('target',0)} time={exits.get('time',0)}")

    # ── Trend tier breakdown for baseline ─────────────────────────────────────
    print(f"\n{'='*60}")
    print("V2 基准 — 趋势层级分布 (验证 trap/neutral/uptrend 比例和质量)")
    print(f"{'='*60}")
    _tier_breakdown(all_trades[versions[0][0]])

    # ── Candle quality breakdown ───────────────────────────────────────────────
    for label, *_ in versions:
        trades = all_trades[label]
        if not trades:
            continue
        print(f"\n{label} — K线质量分布:")
        _candle_breakdown(trades)

    # ── RSI breakdown ─────────────────────────────────────────────────────────
    for label, *_ in versions:
        trades = all_trades[label]
        if not trades:
            continue
        print(f"\n{label} — RSI 区间分布:")
        _rsi_breakdown(trades)

    # ── Delta analysis ─────────────────────────────────────────────────────────
    if len(all_stats) >= 3 and all(s["n"] > 0 for s in all_stats[:3]):
        vbase, v12, v13 = all_stats[:3]
        print(f"\n{'='*60}")
        print("趋势过滤器 Delta 分析")
        print(f"{'='*60}")

        def _delta_row(desc, a, b):
            dn = b["n"] - a["n"]
            de = b["exp_value"] - a["exp_value"]
            flag = "✓" if de > 0.05 else ("✗" if de < -0.05 else "≈")
            print(f"{flag} {desc}: 笔数 {a['n']} → {b['n']} ({dn:+d}), "
                  f"WR {a['win_rate']:.1f}%→{b['win_rate']:.1f}%, "
                  f"期望值 {a['exp_value']:+.2f}%→{b['exp_value']:+.2f}% (Δ{de:+.2f}%)")

        _delta_row("去掉陷阱 (V2→V12)", vbase, v12)
        _delta_row("仅上升趋势 (V2→V13)", vbase, v13)
        print()
        # Show trap-only stats
        trap_trades  = [t for t in all_trades[versions[0][0]] if t["trend_tier"] == "trap"]
        uptrend_only = [t for t in all_trades[versions[0][0]] if t["trend_tier"] == "uptrend"]
        neutral_only = [t for t in all_trades[versions[0][0]] if t["trend_tier"] == "neutral"]
        if trap_trades:
            s = _stats(trap_trades, "TRAP only")
            print(f"  TRAP 子集:     {s['n']} 笔, WR={s['win_rate']:.1f}%, EV={s['exp_value']:+.2f}%")
        if neutral_only:
            s = _stats(neutral_only, "NEUTRAL only")
            print(f"  NEUTRAL 子集:  {s['n']} 笔, WR={s['win_rate']:.1f}%, EV={s['exp_value']:+.2f}%")
        if uptrend_only:
            s = _stats(uptrend_only, "UPTREND only")
            print(f"  UPTREND 子集:  {s['n']} 笔, WR={s['win_rate']:.1f}%, EV={s['exp_value']:+.2f}%")


if __name__ == "__main__":
    main()
