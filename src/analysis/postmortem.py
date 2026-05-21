"""
Post-Mortem Engine
==================
Builds a weekly (or custom-period) trade review by:
  1. Loading executed trades from trades.json for the requested window
  2. Fetching current price for open positions to compute unrealized PnL
  3. Computing trend_tier for each trade symbol (UPTREND / NEUTRAL / TRAP)
  4. Picking top-N winners and losers
  5. Sending full context to Claude for self-critique of prompt quality
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from typing import Optional

import anthropic
import yfinance as yf

from src.config import get_anthropic_key

TRADES_FILE = os.path.join(os.path.dirname(__file__), "../../data/trades.json")


# ── Data loading ──────────────────────────────────────────────────────────────

def _load_trades() -> list[dict]:
    try:
        with open(TRADES_FILE) as f:
            raw = json.load(f)
        if isinstance(raw, dict):
            return list(raw.values())
        return raw
    except Exception:
        return []


def _current_price(symbol: str) -> float | None:
    try:
        ticker = yf.Ticker(symbol)
        hist = ticker.history(period="2d")
        if not hist.empty:
            return float(hist["Close"].iloc[-1])
    except Exception:
        pass
    return None


def _enrich_pnl(trade: dict) -> dict:
    """Add pnl_pct to a trade. Uses stored exit_price if available, else fetches live."""
    t = dict(trade)

    if t.get("pnl_pct") is not None:
        t["pnl_source"] = "closed"
        return t

    fill_price = t.get("fill_price") or t.get("price_at_approve") or t.get("price")
    if not fill_price:
        return t

    exit_price = t.get("exit_price") or _current_price(t["symbol"])
    if exit_price:
        t["pnl_pct"] = round((exit_price - fill_price) / fill_price * 100, 2)
        t["current_price"] = round(exit_price, 2)
        t["pnl_source"] = "live"
    return t


# ── Trend tier ────────────────────────────────────────────────────────────────

def _compute_tier_for_symbols(symbols: list[str]) -> dict[str, str]:
    """Batch-download 90d price history and classify each symbol into UPTREND/NEUTRAL/TRAP."""
    if not symbols:
        return {}
    result = {}
    try:
        syms = list(set(symbols))
        raw = yf.download(
            syms if len(syms) > 1 else syms[0],
            period="90d",
            auto_adjust=True,
            progress=False,
            group_by="ticker" if len(syms) > 1 else None,
        )
    except Exception:
        return {s: "neutral" for s in symbols}

    for sym in syms:
        try:
            if len(syms) == 1:
                closes = raw["Close"].dropna()
            else:
                if sym not in raw.columns.get_level_values(0):
                    result[sym] = "neutral"
                    continue
                closes = raw[sym]["Close"].dropna()

            if len(closes) < 20:
                result[sym] = "neutral"
                continue

            price   = float(closes.iloc[-1])
            ma50    = float(closes.rolling(50).mean().iloc[-1]) if len(closes) >= 50 else None
            vs_ma50 = (price - ma50) / ma50 * 100 if ma50 else 0.0

            price_3m    = float(closes.iloc[-63]) if len(closes) >= 63 else float(closes.iloc[0])
            momentum_3m = (price - price_3m) / price_3m * 100

            if vs_ma50 < -8 and momentum_3m < -20:
                result[sym] = "trap"
            elif vs_ma50 > 0 and momentum_3m > -10:
                result[sym] = "uptrend"
            else:
                result[sym] = "neutral"
        except Exception:
            result[sym] = "neutral"

    # fill any missing
    for sym in symbols:
        result.setdefault(sym, "neutral")
    return result


def _tier_stats(trades: list[dict]) -> dict:
    """Aggregate PnL stats per trend_tier."""
    from collections import defaultdict
    buckets: dict[str, list[float]] = defaultdict(list)
    for t in trades:
        tier = t.get("trend_tier", "neutral")
        pnl  = t.get("pnl_pct")
        if pnl is not None:
            buckets[tier].append(pnl)

    out = {}
    for tier in ("uptrend", "neutral", "trap"):
        pnls = buckets.get(tier, [])
        if not pnls:
            out[tier] = {"count": 0, "win_rate": None, "avg_pnl": None}
            continue
        out[tier] = {
            "count":    len(pnls),
            "win_rate": round(sum(1 for p in pnls if p > 0) / len(pnls) * 100, 1),
            "avg_pnl":  round(sum(pnls) / len(pnls), 2),
        }
    return out


# ── Timeline breakdown ───────────────────────────────────────────────────────

def _timeline_breakdown(trades: list[dict], days: int) -> list[dict]:
    """Split enriched trades into time sub-periods (weeks or months)."""
    if days <= 7 or not trades:
        return []

    now = datetime.now(timezone.utc)

    if days <= 14:
        bucket_days = 7
        n_buckets   = 2
    elif days <= 30:
        bucket_days = 7
        n_buckets   = 4
    else:
        bucket_days = 30
        n_buckets   = 3

    unit = "周" if bucket_days == 7 else "月"
    result = []

    for i in range(n_buckets):  # i=0 → most recent
        bucket_end   = now - timedelta(days=i * bucket_days)
        bucket_start = now - timedelta(days=(i + 1) * bucket_days)

        if i == 0:
            label = f"第 {n_buckets} {unit}（本{unit}）"
        elif i == n_buckets - 1:
            label = f"第 1 {unit}（最早）"
        else:
            label = f"第 {n_buckets - i} {unit}"

        bucket_trades = []
        for t in trades:
            created = t.get("created_at", "")
            try:
                dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
                if bucket_start <= dt < bucket_end:
                    bucket_trades.append(t)
            except Exception:
                pass

        pnls   = [t["pnl_pct"] for t in bucket_trades if t.get("pnl_pct") is not None]
        wins   = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p <= 0]

        if not pnls:
            result.append({"label": label, "count": 0, "win_rate": None, "avg_pnl": None, "ev": None, "trend": "flat"})
            continue

        win_rate = len(wins) / len(pnls) * 100
        avg_win  = sum(wins)   / len(wins)   if wins   else 0.0
        avg_loss = sum(losses) / len(losses) if losses else 0.0
        ev       = win_rate / 100 * avg_win + (1 - win_rate / 100) * avg_loss

        result.append({
            "label":    label,
            "count":    len(pnls),
            "win_rate": round(win_rate, 1),
            "avg_pnl":  round(sum(pnls) / len(pnls), 2),
            "ev":       round(ev, 2),
            "trend":    "tbd",
        })

    # Assign trend vs previous (older) period
    for i in range(len(result)):
        if i == len(result) - 1:
            result[i]["trend"] = "base"
        else:
            prev_ev = result[i + 1].get("ev")
            curr_ev = result[i].get("ev")
            if curr_ev is None or prev_ev is None:
                result[i]["trend"] = "flat"
            elif curr_ev > prev_ev + 0.2:
                result[i]["trend"] = "up"
            elif curr_ev < prev_ev - 0.2:
                result[i]["trend"] = "down"
            else:
                result[i]["trend"] = "flat"

    return result


# ── Period filtering ──────────────────────────────────────────────────────────

def _trades_in_window(trades: list[dict], days: int) -> list[dict]:
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    result = []
    for t in trades:
        if t.get("status") not in ("executed",):
            continue
        if t.get("fill_status") != "filled":
            continue
        created = t.get("created_at", "")
        try:
            dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
            if dt >= cutoff:
                result.append(t)
        except Exception:
            pass
    return result


# ── Prompt builder ────────────────────────────────────────────────────────────

def _fmt_trade(t: dict, rank: int) -> str:
    pnl    = t.get("pnl_pct")
    pnl_s  = f"{pnl:+.1f}%" if pnl is not None else "N/A"
    source = t.get("pnl_source", "")
    src_tag = "(浮动)" if source == "live" else "(已平)" if source == "closed" else ""
    tier   = t.get("trend_tier", "?")

    stop   = t.get("stop_loss")
    target = t.get("target_price")
    fill   = t.get("fill_price") or t.get("price")
    cur    = t.get("current_price", "?")

    stop_dist   = f"{(fill - stop)   / fill * 100:.1f}%" if fill and stop   else "N/A"
    target_dist = f"{(target - fill) / fill * 100:.1f}%" if fill and target else "N/A"

    return (
        f"{rank}. {t['symbol']} | tier={tier} | signal={t.get('signal','?')} | "
        f"confidence={t.get('confidence','?')} | "
        f"rsi={t.get('rsi','?')} | mom_5d={t.get('momentum_5d','?')} | "
        f"vol_ratio={t.get('volume_ratio','?')} | "
        f"fill=${fill} → now=${cur} | pnl={pnl_s}{src_tag} | "
        f"stop_dist={stop_dist} | target_dist={target_dist}\n"
        f"   Claude 当时的判断: {t.get('reason','(无)')}"
    )


def _build_prompt(
    winners: list[dict],
    losers:  list[dict],
    days:    int,
    tier_stats: dict,
) -> str:
    w_section = "\n\n".join(_fmt_trade(t, i + 1) for i, t in enumerate(winners))
    l_section = "\n\n".join(_fmt_trade(t, i + 1) for i, t in enumerate(losers))

    tier_lines = []
    for tier in ("uptrend", "neutral", "trap"):
        s = tier_stats.get(tier, {})
        if s.get("count", 0) == 0:
            continue
        tier_lines.append(
            f"  {tier.upper():<8}: {s['count']} 笔, 胜率={s['win_rate']}%, 均PnL={s['avg_pnl']:+.2f}%"
        )
    tier_section = "\n".join(tier_lines) if tier_lines else "（无数据）"

    return f"""你是一个量化交易策略的 AI 顾问，正在对过去 {days} 天的交易做复盘。

【趋势层级归因（全部已成交交易）】
{tier_section}

以下是表现最好的 {len(winners)} 笔交易（赢家组）：
{w_section or '（无数据）'}

以下是表现最差的 {len(losers)} 笔交易（亏损组）：
{l_section or '（无数据）'}

请完成以下分析（用中文，结构清晰）：

## 1. 趋势层级归因
根据上方 UPTREND / NEUTRAL / TRAP 分布，哪个层级贡献了主要亏损？TRAP 陷阱过滤规则是否有效？

## 2. 赢家共同特征
赢家组在 tier / signal / confidence / RSI / momentum / 判断逻辑 上有什么共同点？

## 3. 亏损根因分析
逐一分析每笔亏损交易：当时 Claude 给出的判断现在看有什么问题？是信息缺失、过度乐观、还是逻辑漏洞？

## 4. 系统性偏差
从所有交易对比看，AI 评分存在哪些系统性偏差？（例如：对某类股票过于乐观 / 对某类 catalyst 判断失准 / 某个 RSI 区间表现差但仍被高分）

## 5. Prompt 改进建议
给出 2-4 条**具体可执行**的 prompt 改进建议，格式：
- 问题：[描述偏差]
- 建议：[具体的 prompt 文字修改方向]

直接输出分析，不需要开场白。"""


# ── Main entry ────────────────────────────────────────────────────────────────

def run_postmortem(days: int = 7, top_n: int = 3) -> dict:
    """
    Run post-mortem for the past `days` days.
    Returns dict with trades, stats, tier_breakdown, and Claude analysis.
    """
    all_trades = _load_trades()
    window = _trades_in_window(all_trades, days)

    if not window:
        return {
            "days": days,
            "total": 0,
            "enriched": 0,
            "winners": [],
            "losers": [],
            "stats": {},
            "tier_breakdown": {},
            "analysis": "该时间段内没有已成交的交易记录。",
            "error": None,
            "generated_at": datetime.now().isoformat(),
        }

    # Enrich with PnL
    enriched = []
    for t in window:
        e = _enrich_pnl(t)
        if e.get("pnl_pct") is not None:
            enriched.append(e)

    # Compute trend tier for all unique symbols
    symbols = list({t["symbol"] for t in enriched})
    tier_map = _compute_tier_for_symbols(symbols)
    for t in enriched:
        t["trend_tier"] = tier_map.get(t["symbol"], "neutral")

    enriched.sort(key=lambda x: x["pnl_pct"])
    losers  = enriched[:top_n]
    winners = list(reversed(enriched[-top_n:]))

    # Stats
    pnls = [t["pnl_pct"] for t in enriched]
    stats = {
        "total_enriched": len(enriched),
        "total_window":   len(window),
        "win_rate":       round(sum(1 for p in pnls if p > 0) / len(pnls) * 100, 1) if pnls else 0,
        "avg_pnl":        round(sum(pnls) / len(pnls), 2) if pnls else 0,
        "best_pnl":       round(max(pnls), 2) if pnls else 0,
        "worst_pnl":      round(min(pnls), 2) if pnls else 0,
    }

    tier_breakdown     = _tier_stats(enriched)
    timeline_breakdown = _timeline_breakdown(enriched, days)

    # Claude analysis
    analysis = ""
    error = None
    try:
        client = anthropic.Anthropic(api_key=get_anthropic_key())
        prompt = _build_prompt(winners, losers, days, tier_breakdown)
        msg = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=2048,
            messages=[{"role": "user", "content": prompt}],
        )
        analysis = msg.content[0].text
    except Exception as e:
        error = str(e)
        analysis = f"Claude 分析失败：{e}"

    return {
        "days":           days,
        "total":          len(window),
        "enriched":       len(enriched),
        "winners":        winners,
        "losers":         losers,
        "stats":          stats,
        "tier_breakdown":     tier_breakdown,
        "timeline_breakdown": timeline_breakdown,
        "analysis":           analysis,
        "error":          error,
        "generated_at":   datetime.now().isoformat(),
    }
