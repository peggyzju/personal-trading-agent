"""K 线分析 — v8 核心指标解读(机械门控 + AI 一句话点评)。

给前端「K线分析弹窗」用:返回蜡烛 + MA20/MA50/RSI 序列 + 4 个 v8 趋势门通过情况
+ 量能参考 + AI 一句话点评(按 symbol+ET日 缓存,不重复烧钱)。
仅作展示解读,不参与买卖决策(对齐 v8 纯机械)。
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Optional

import pandas as pd

_AI_CACHE_FILE = Path(__file__).parent.parent.parent / "data" / "kline_ai_cache.json"
_CANDLES_MAX = 90   # 主图最多展示 90 个交易日


def _et_today() -> str:
    import zoneinfo
    return datetime.now(zoneinfo.ZoneInfo("America/New_York")).date().isoformat()


def _rsi_series(closes: pd.Series, period: int = 14) -> pd.Series:
    delta = closes.diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    rs = gain / loss.replace(0, pd.NA)
    return 100 - 100 / (1 + rs)


def _round_or_none(v) -> Optional[float]:
    try:
        if v is None or pd.isna(v):
            return None
        return round(float(v), 2)
    except Exception:
        return None


def _momentum_rank(symbol: str, candidates: list[dict] | None) -> Optional[int]:
    """在当日扫描候选里按 3 月动量的排名(1-based);不在候选/无扫描则 None。"""
    if not candidates:
        return None
    ranked = sorted(
        [c for c in candidates if (c.get("momentum_3m") is not None)],
        key=lambda c: c.get("momentum_3m") or 0, reverse=True,
    )
    for i, c in enumerate(ranked):
        if c.get("symbol") == symbol:
            return i + 1
    return None


def _ai_comment(symbol: str, gates: list[dict], vol_info: dict, tech: dict) -> str:
    """AI 一句话点评。按 symbol+ET日 缓存。失败则机械兜底,绝不让弹窗开不出来。"""
    key = f"{symbol}:{_et_today()}"
    try:
        if _AI_CACHE_FILE.exists():
            cache = json.loads(_AI_CACHE_FILE.read_text())
            if key in cache:
                return cache[key]
    except Exception:
        cache = {}

    readout = "; ".join(f"{g['label']}{'✓' if g['pass'] else '✗'} {g['value']}" for g in gates)
    readout += f"; 量能 {vol_info['value']}"
    passed = sum(1 for g in gates if g["pass"])

    comment = ""
    try:
        from src.config import get_anthropic_client
        client = get_anthropic_client(timeout=20.0, max_retries=1)
        msg = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=120,
            messages=[{
                "role": "user",
                "content": (
                    f"你是动量交易助手。下面是 {symbol} 的 v8 核心指标读出(共 4 门过 {passed} 门):\n"
                    f"{readout}\n\n"
                    "用一句话(≤40 字,中文)点评:趋势强弱、当前是否健康回调、放量是否配合。"
                    "只描述形态,不给买卖建议、不加'建议/可'字样。直接输出这句话,不要前后缀。"
                ),
            }],
        )
        comment = (msg.content[0].text or "").strip().strip('"').replace("\n", " ")
    except Exception:
        comment = ""

    if not comment:
        # 机械兜底
        trend = next((g for g in gates if g["key"] == "trend"), {})
        if passed == 4:
            comment = f"4 门全过的强势上升趋势,{'放量' if vol_info.get('is_high') else '量能平稳'},符合 v8 入场形态。"
        else:
            failed = [g["label"] for g in gates if not g["pass"]]
            comment = f"趋势门未全过(缺:{'、'.join(failed)}),暂不符合 v8 入场,需等结构修复。"

    try:
        cache = {}
        if _AI_CACHE_FILE.exists():
            cache = json.loads(_AI_CACHE_FILE.read_text())
        cache[key] = comment
        _AI_CACHE_FILE.parent.mkdir(exist_ok=True)
        _AI_CACHE_FILE.write_text(json.dumps(cache, ensure_ascii=False))
    except Exception:
        pass
    return comment


def build_kline_analysis(symbol: str, candidates: list[dict] | None = None,
                         name: str = "") -> dict:
    """组装 K 线分析数据。symbol 大写。candidates 传当日扫描候选(算排名 + 取名字)。"""
    symbol = symbol.upper().strip()
    from src.monitor.price_monitor import get_ohlcv
    from src.monitor.sp500_scanner import compute_technicals

    df = get_ohlcv(symbol, period="6mo")
    if df is None or len(df) < 30:
        return {"error": f"无法获取 {symbol} 的行情数据(数据不足)"}

    closes = df["Close"]
    ma20_s = closes.rolling(20).mean()
    ma50_s = closes.rolling(50).mean()
    rsi_s  = _rsi_series(closes)

    tail = df.tail(_CANDLES_MAX)
    idx = tail.index
    candles = [{
        "t": (ts.date().isoformat() if hasattr(ts, "date") else str(ts)[:10]),
        "o": round(float(r["Open"]), 2), "h": round(float(r["High"]), 2),
        "l": round(float(r["Low"]), 2),  "c": round(float(r["Close"]), 2),
        "v": int(r["Volume"]),
    } for ts, r in tail.iterrows()]
    ma20 = [_round_or_none(v) for v in ma20_s.tail(_CANDLES_MAX)]
    ma50 = [_round_or_none(v) for v in ma50_s.tail(_CANDLES_MAX)]
    rsi  = [_round_or_none(v) for v in rsi_s.tail(_CANDLES_MAX)]

    tech = compute_technicals(df)
    price       = tech.get("price") or round(float(closes.iloc[-1]), 2)
    ma50_now    = _round_or_none(ma50_s.iloc[-1]) or 0.0
    ma50_slope  = tech.get("ma50_slope_pct") or 0.0
    rsi_now     = tech.get("rsi") or 50.0
    mom_3m      = tech.get("momentum_3m") or 0.0
    vs_ma20     = tech.get("vs_ma20_pct") or 0.0
    vs_ma50     = tech.get("vs_ma50_pct")
    vol_ratio   = tech.get("volume_ratio") or 1.0
    rank        = _momentum_rank(symbol, candidates)

    # ── v8 趋势门:4 个硬条件(精确匹配 sp500_scanner 的 trend_ok)──────────────
    g_trend = (vs_ma50 is not None and vs_ma50 > 0) and ma50_slope > 0
    g_rsi   = 50 <= rsi_now <= 80
    g_mom   = mom_3m > 0
    g_ext   = vs_ma20 <= 15.0

    gates = [
        {"key": "trend", "label": "趋势", "pass": bool(g_trend),
         "value": f"价 ${price:.0f} {'>' if (vs_ma50 or 0) > 0 else '<'} MA50 ${ma50_now:.0f} · MA50 5日 {ma50_slope:+.1f}%"},
        {"key": "rsi", "label": "RSI", "pass": bool(g_rsi),
         "value": f"{rsi_now:.0f} · " + ("强势区 50–80" if g_rsi else ("超卖 <50" if rsi_now < 50 else "过热 >80"))},
        {"key": "momentum", "label": "动量", "pass": bool(g_mom),
         "value": f"3月 {mom_3m:+.0f}%" + (f" · 排名 #{rank}" if rank else "")},
        {"key": "extension", "label": "延伸", "pass": bool(g_ext),
         "value": f"vs_ma20 {vs_ma20:+.0f}% · " + ("≤15% 不过高" if g_ext else ">15% 过度延伸")},
    ]
    vol_info = {
        "label": "量能",
        "value": f"量比 {vol_ratio:.1f} · " + ("放量" if vol_ratio >= 1.5 else ("缩量" if vol_ratio < 0.7 else "常量")),
        "is_high": vol_ratio >= 1.5,
    }
    passed = sum(1 for g in gates if g["pass"])
    summary = {"passed": passed, "total": 4, "v8_eligible": passed == 4}

    return {
        "symbol": symbol,
        "name": name or "",
        "rank": rank,
        "candles": candles,
        "ma20": ma20, "ma50": ma50, "rsi": rsi,
        "indicators": {
            "price": price, "ma50": ma50_now, "ma50_slope_pct": round(ma50_slope, 2),
            "rsi": rsi_now, "momentum_3m": round(mom_3m, 2),
            "vs_ma20": round(vs_ma20, 2), "volume_ratio": round(vol_ratio, 2),
        },
        "gates": gates,
        "volume_info": vol_info,
        "summary": summary,
        "ai_comment": _ai_comment(symbol, gates, vol_info, tech),
        "as_of": _et_today(),
    }
