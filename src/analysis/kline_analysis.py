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


_ANALYSIS_FRAMEWORK = """\
你是动量交易的技术分析助手。请按下面的「动量分析框架」对该股做结构化点评。

【分析框架——必须逐条覆盖,且全部基于给到的数据,不要套话】
1. 动量分尺度:中期(3月动量 + MA50 斜率)vs 短期(1月动量 + 最近几根)。
   关键认知:一两根 K 线不代表整个动量;中期还在不等于短期没受伤。
2. 回调/下跌的量价性质——最重要的判别:
   - 缩量回踩均线 = 健康回调(只是获利了结,无恐慌抛压)
   - 放量跌破均线 = 分布/出货警告(真实抛压,不是健康回调)
3. 反弹/收复的质量:放量站稳均线 = 有承接(强);缩量收复 = 信心不足(弱,易得而复失)。
4. 定性 + 观察点:判断当前属于【健康回调 / 高位压力测试 / 动量转弱】之一,
   并给出 1 个最关键的后续观察点(通常围绕 MA50 能否放量站稳 / 会不会再放量破位)。

【输出要求 · 严格遵守】
- 严格输出 3 行,每行以固定标签开头,中文,每行 ≤ 38 字:
  动能:<中期 vs 短期 动量状态>
  量价:<最近这波下跌/反弹是放量还是缩量,代表分布还是健康>
  观察:<当前定性 + 1 个最关键观察点>
- 只描述形态与动量,绝不给买卖建议,不出现"建议/可以/应该/买入/卖出/加仓/减仓"等字样。
- 直接输出这 3 行,不要前后缀、不要复述框架、不要解释。
"""


def _ai_comment(symbol: str, gates: list[dict], vol_info: dict,
                ind: dict, recent_path: list[dict]) -> str:
    """AI 结构化点评(3行:动能/量价/观察)。按 symbol+ET日 缓存,失败机械兜底。"""
    key = f"{symbol}:{_et_today()}:v2"   # v2:换了框架,旧的一句话缓存不再复用
    try:
        if _AI_CACHE_FILE.exists():
            cache = json.loads(_AI_CACHE_FILE.read_text())
            if key in cache:
                return cache[key]
    except Exception:
        pass

    passed = sum(1 for g in gates if g["pass"])
    path_lines = "\n".join(
        f"  {p['date'][5:]} 收${p['close']} 距MA50 {('%+.1f%%' % p['vs_ma50']) if p['vs_ma50'] is not None else 'NA'} "
        f"量{p['vol_x']}x{' 放量' if (p['vol_x'] or 0) >= 1.5 else (' 缩量' if (p['vol_x'] or 9) < 0.7 else '')}"
        for p in recent_path
    )
    data_block = (
        f"标的:{symbol}\n"
        f"【核心指标】\n"
        f"现价 ${ind['price']} | MA50 ${ind['ma50']}(价在上方 {ind['vs_ma50']:+.1f}%,MA50 5日斜率 {ind['ma50_slope_pct']:+.1f}%)\n"
        f"RSI {ind['rsi']:.0f} | 3月动量 {ind['momentum_3m']:+.0f}%(中期) | "
        f"1月动量 {ind['momentum_1m']:+.0f}%(短期) | vs_MA20 {ind['vs_ma20']:+.1f}%\n"
        f"v8 趋势门:4 门过 {passed} 门 "
        f"({'、'.join((g['label'] + ('✓' if g['pass'] else '✗')) for g in gates)})\n"
        f"【最近 10 根 收盘 / 相对MA50 / 量(相对20日均量)】\n{path_lines}\n"
    )

    comment = ""
    try:
        from src.config import get_anthropic_client
        client = get_anthropic_client(timeout=30.0, max_retries=1)
        msg = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=300,
            messages=[{"role": "user", "content": _ANALYSIS_FRAMEWORK + "\n\n" + data_block}],
        )
        comment = (msg.content[0].text or "").strip()
    except Exception:
        comment = ""

    if not comment:
        comment = _mechanical_comment(ind, gates, recent_path, passed)

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


def _mechanical_comment(ind: dict, gates: list[dict], recent_path: list[dict], passed: int) -> str:
    """AI 不可用时的机械兜底,也走 3 行框架(动能/量价/观察)。"""
    mom3m, mom1m = ind["momentum_3m"], ind["momentum_1m"]
    mid = "强" if mom3m > 50 else ("中性" if mom3m > 0 else "弱")
    short = "延续" if mom1m >= 0 else "走弱"
    # 找最近最大单日下跌及其量
    downs = [p for p in recent_path[1:] if p["vs_ma50"] is not None]
    big_down = min(recent_path, key=lambda p: (p["close"] - (recent_path[recent_path.index(p) - 1]["close"]
               if recent_path.index(p) > 0 else p["close"]))) if len(recent_path) > 1 else None
    last = recent_path[-1] if recent_path else {}
    vp = (f"近端下杀量{big_down['vol_x']}x(" + ("放量分布" if (big_down['vol_x'] or 0) >= 1.5 else "缩量,偏健康") + ")"
          ) if big_down else "量价数据不足"
    elig = passed == 4
    obs = ("4门全过、趋势在,盯 MA50 能否放量站稳" if elig
           else f"门控缺{'、'.join(g['label'] for g in gates if not g['pass'])},盯能否修复站回 MA50")
    return (f"动能:中期{mid}(3月{mom3m:+.0f}%),短期{short}(1月{mom1m:+.0f}%)\n"
            f"量价:{vp};今收量{last.get('vol_x','NA')}x\n"
            f"观察:{obs}")


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
    mom_1m      = tech.get("momentum_1m") or 0.0
    rank        = _momentum_rank(symbol, candidates)

    # 最近 10 根量价路径 —— 喂给 AI 判断"放量跌破 vs 缩量回调 / 反弹质量"
    vols_all = df["Volume"].dropna()
    vol_avg20 = float(vols_all.iloc[-22:-2].mean()) if len(vols_all) >= 22 else float(vols_all.mean())
    recent_path = []
    for ts, r in df.tail(10).iterrows():
        c_ = float(r["Close"]); v_ = float(r["Volume"]); m_ = ma50_s.get(ts)
        recent_path.append({
            "date": (ts.date().isoformat() if hasattr(ts, "date") else str(ts)[:10]),
            "close": round(c_, 2),
            "vs_ma50": (round((c_ / float(m_) - 1) * 100, 1) if (m_ is not None and not pd.isna(m_) and m_) else None),
            "vol_x": round(v_ / vol_avg20, 1) if vol_avg20 else None,
        })

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
        "ai_comment": _ai_comment(symbol, gates, vol_info, {
            "price": price, "ma50": ma50_now, "ma50_slope_pct": ma50_slope,
            "rsi": rsi_now, "momentum_3m": mom_3m, "momentum_1m": mom_1m,
            "vs_ma20": vs_ma20, "vs_ma50": vs_ma50 if vs_ma50 is not None else 0.0,
            "volume_ratio": vol_ratio,
        }, recent_path),
        "as_of": _et_today(),
    }
