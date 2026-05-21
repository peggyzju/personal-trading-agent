"""
Post-Mortem Engine
==================
Builds a weekly (or custom-period) trade review by:
  1. Loading executed trades from trades.json for the requested window
  2. Fetching current price for open positions to compute unrealized PnL
  3. Picking top-N winners and losers
  4. Sending full context to Claude for self-critique of prompt quality
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

    stop   = t.get("stop_loss")
    target = t.get("target_price")
    fill   = t.get("fill_price") or t.get("price")
    cur    = t.get("current_price", "?")

    stop_dist  = f"{(fill - stop)  / fill * 100:.1f}%" if fill and stop  else "N/A"
    target_dist = f"{(target - fill) / fill * 100:.1f}%" if fill and target else "N/A"

    return (
        f"{rank}. {t['symbol']} | signal={t.get('signal','?')} | "
        f"confidence={t.get('confidence','?')} | "
        f"rsi={t.get('rsi','?')} | mom_5d={t.get('momentum_5d','?')} | "
        f"vol_ratio={t.get('volume_ratio','?')} | "
        f"fill=${fill} → now=${cur} | pnl={pnl_s}{src_tag} | "
        f"stop_dist={stop_dist} | target_dist={target_dist}\n"
        f"   Claude 当时的判断: {t.get('reason','(无)')}"
    )


def _build_prompt(winners: list[dict], losers: list[dict], days: int) -> str:
    w_section = "\n\n".join(_fmt_trade(t, i + 1) for i, t in enumerate(winners))
    l_section = "\n\n".join(_fmt_trade(t, i + 1) for i, t in enumerate(losers))

    return f"""你是一个量化交易策略的 AI 顾问，正在对过去 {days} 天的交易做复盘。

以下是表现最好的 {len(winners)} 笔交易（赢家组）：
{w_section or '（无数据）'}

以下是表现最差的 {len(losers)} 笔交易（亏损组）：
{l_section or '（无数据）'}

请完成以下分析（用中文，结构清晰）：

## 1. 赢家共同特征
赢家组在 signal / confidence / RSI / momentum / 判断逻辑 上有什么共同点？

## 2. 亏损根因分析
逐一分析每笔亏损交易：当时 Claude 给出的判断现在看有什么问题？是信息缺失、过度乐观、还是逻辑漏洞？

## 3. 系统性偏差
从所有交易对比看，AI 评分存在哪些系统性偏差？（例如：对某类股票过于乐观 / 对某类 catalyst 判断失准 / 某个 RSI 区间表现差但仍被高分）

## 4. Prompt 改进建议
给出 2-4 条**具体可执行**的 prompt 改进建议，格式：
- 问题：[描述偏差]
- 建议：[具体的 prompt 文字修改方向]

直接输出分析，不需要开场白。"""


# ── Main entry ────────────────────────────────────────────────────────────────

def run_postmortem(days: int = 7, top_n: int = 3) -> dict:
    """
    Run post-mortem for the past `days` days.
    Returns dict with trades, stats, and Claude analysis.
    """
    all_trades = _load_trades()
    window = _trades_in_window(all_trades, days)

    if not window:
        return {
            "days": days,
            "total": 0,
            "winners": [],
            "losers": [],
            "stats": {},
            "analysis": "该时间段内没有已成交的交易记录。",
            "error": None,
        }

    # Enrich with PnL
    enriched = []
    for t in window:
        e = _enrich_pnl(t)
        if e.get("pnl_pct") is not None:
            enriched.append(e)

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

    # Claude analysis
    analysis = ""
    error = None
    try:
        client = anthropic.Anthropic(api_key=get_anthropic_key())
        prompt = _build_prompt(winners, losers, days)
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
        "days":     days,
        "total":    len(window),
        "enriched": len(enriched),
        "winners":  winners,
        "losers":   losers,
        "stats":    stats,
        "analysis": analysis,
        "error":    error,
        "generated_at": datetime.now().isoformat(),
    }
