"""财报雷达 Earnings Radar — 事件驱动子系统。

三块:
1. build_calendar()           — 全市场未来 N 天财报日历(挂 Maya 每日生成),持仓优先,缓存。
2. historical_reactions()     — 某票过去几次财报后的 价格反应 + EPS 超预期(给研判当背景)。
3. detect_reactions()         — 当日发财报名单的 价格反应检测(跳空/放量)→ 触发研判。
4. analyze_earnings()         — 组装数据 + Claude 研判(入场/持仓建议),人工决策,不自动下单。

数据源 yfinance(免费,有缺失/滞后);价格反应用 Alpaca 日线(更可靠)。
"""
from __future__ import annotations

import json
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

_FINNHUB_BASE = "https://finnhub.io/api/v1"


def _finnhub_get(path: str, **params) -> dict | list | None:
    """调 Finnhub API。失败返回 None。数据源:财报日历 / 历史 EPS 超预期(当前、含本年)。"""
    from src.config import get_finnhub_key
    key = get_finnhub_key()
    if not key:
        return None
    params["token"] = key
    url = f"{_FINNHUB_BASE}{path}?{urllib.parse.urlencode(params)}"
    try:
        with urllib.request.urlopen(url, timeout=20) as r:
            return json.loads(r.read())
    except Exception:
        return None


_ROOT = Path(__file__).resolve().parent.parent.parent
_UNIVERSE_FILE = _ROOT / "data" / "sp500_constituents.txt"
_CALENDAR_FILE = _ROOT / "data" / "earnings_calendar.json"
_ANALYSIS_FILE = _ROOT / "data" / "earnings_analysis.json"
_WATCHLIST_FILE = _ROOT / "data" / "watchlist.json"

GAP_TRIGGER_PCT = 4.0   # 隔夜/盘后跳空 ≥ 此值 视为显著财报反应


# ── 工具 ──────────────────────────────────────────────────────────────────────
def _load_universe() -> list[str]:
    if _UNIVERSE_FILE.exists():
        return sorted(set(_UNIVERSE_FILE.read_text().split()))
    return []


def _portfolio_symbols() -> set[str]:
    try:
        from src.trader.alpaca_trader import get_client
        return {p.symbol for p in get_client().list_positions()}
    except Exception:
        return set()


def _watchlist_symbols() -> set[str]:
    try:
        if _WATCHLIST_FILE.exists():
            d = json.loads(_WATCHLIST_FILE.read_text())
            return set(d if isinstance(d, list) else d.get("symbols", []))
    except Exception:
        pass
    return set()


# ── Part A: 财报日历 ──────────────────────────────────────────────────────────
def build_calendar(days: int = 7) -> dict:
    """全市场未来 days 天财报日历(Finnhub 批量,一次调用)。持仓优先标红。
    写 data/earnings_calendar.json。带真实盘前/盘后(BMO/AMC)。"""
    universe = set(_load_universe())
    holdings = _portfolio_symbols()
    watch = _watchlist_symbols()
    relevant = universe | holdings   # 过滤到我们关心的票(Finnhub 返回全市场含小盘)
    today = date.today()
    horizon = today + timedelta(days=days)

    data = _finnhub_get("/calendar/earnings",
                        **{"from": today.isoformat(), "to": horizon.isoformat()})
    cal = (data or {}).get("earningsCalendar", []) if isinstance(data, dict) else []

    rows: list[dict] = []
    seen = set()
    for e in cal:
        sym = e.get("symbol")
        ed = e.get("date")
        if not sym or not ed or sym not in relevant or sym in seen:
            continue
        seen.add(sym)
        session = {"bmo": "BMO", "amc": "AMC"}.get((e.get("hour") or "").lower(), "?")
        rows.append({
            "symbol": sym,
            "date": ed,
            "session": session,
            "eps_estimate": e.get("epsEstimate"),
            "in_portfolio": sym in holdings,
            "importance": ("持仓" if sym in holdings else "关注" if sym in watch else ""),
            "days_until": (date.fromisoformat(ed) - today).days,
        })

    # 排序:持仓优先 → 距今天数 → 字母
    rows.sort(key=lambda r: (not r["in_portfolio"], r["days_until"], r["symbol"]))
    out = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "horizon_days": days,
        "count": len(rows),
        "holdings_reporting": sum(1 for r in rows if r["in_portfolio"]),
        "rows": rows,
    }
    _CALENDAR_FILE.write_text(json.dumps(out, indent=2))
    return out


# ── Part B 背景: 历史财报后反应 ───────────────────────────────────────────────
def historical_reactions(symbol: str, n: int = 4) -> list[dict]:
    """过去 n 次财报:EPS 超预期%(Finnhub /stock/earnings,当前、含本年)+
    财报后价格反应%(财季窗口内成交量最大那天的跳空 = 财报反应日,Alpaca)。"""
    out: list[dict] = []
    earnings = _finnhub_get("/stock/earnings", symbol=symbol)
    if not isinstance(earnings, list) or not earnings:
        return out
    from src.trader.alpaca_trader import get_client
    client = get_client()
    for e in earnings[:n]:
        period = e.get("period")
        if not period:
            continue
        try:
            pd_ = date.fromisoformat(period)
        except Exception:
            continue
        surprise = e.get("surprisePercent")
        move = None
        try:
            end_d = min(pd_ + timedelta(days=20), date.today())
            bars = client.get_bars(
                symbol, "1Day",
                start=(pd_ - timedelta(days=40)).isoformat(),
                end=end_d.isoformat(), feed="iex",
            ).df
            vols = bars["volume"].tolist()
            closes = bars["close"].tolist()
            if len(vols) >= 2:
                # 财报公布日 = 该财季窗口内成交量最大的那天 → 算它相对前一日的跳空
                mi = max(range(1, len(vols)), key=lambda i: vols[i])
                move = (closes[mi] / closes[mi - 1] - 1) * 100
        except Exception:
            pass
        out.append({
            "date": period,
            "surprise_pct": round(float(surprise), 1) if surprise is not None else None,
            "reaction_pct": round(move, 1) if move is not None else None,
        })
    return out


# ── Part B: 当日财报反应检测 ──────────────────────────────────────────────────
def reaction_ready(earnings_date: str | None, session: str | None) -> bool:
    """财报后跳空是否已可测:隔日(任意时段)/ 当日盘前已开盘 → True;
    当日盘后(AMC)还没出 / 盘前未到 9:30 / 缺日期 → False(避免把盘前价当财报后跳空)。"""
    if not earnings_date:
        return False
    from datetime import time as _dtime
    try:
        from zoneinfo import ZoneInfo
        now_et = datetime.now(ZoneInfo("America/New_York"))
    except Exception:
        now_et = datetime.now()
    try:
        ed = date.fromisoformat(earnings_date)
    except Exception:
        return False
    nd = now_et.date()
    if ed < nd:
        return True
    if ed == nd and session == "BMO" and now_et.time() >= _dtime(9, 30):
        return True
    return False


def detect_reactions(gap_trigger: float = GAP_TRIGGER_PCT) -> list[dict]:
    """日历里 date<=今天 的票,检测显著价格反应(最新价 vs 前一日收盘跳空%)。
    返回触发研判的名单。盘后/盘前数据依赖 Alpaca,免费源可能滞后(诚实局限)。"""
    if not _CALENDAR_FILE.exists():
        return []
    cal = json.loads(_CALENDAR_FILE.read_text())
    today = date.today()
    candidates = [r for r in cal.get("rows", []) if reaction_ready(r.get("date"), r.get("session"))]
    if not candidates:
        return []
    from src.trader.alpaca_trader import get_client
    client = get_client()
    triggered = []
    for r in candidates:
        sym = r["symbol"]
        try:
            bars = client.get_bars(sym, "1Day",
                                   start=(today - timedelta(days=6)).isoformat(), feed="iex").df
            closes = bars["close"].tolist()
            vols = bars["volume"].tolist()
            if len(closes) < 2:
                continue
            gap = (closes[-1] / closes[-2] - 1) * 100
            vol_ratio = vols[-1] / (sum(vols[:-1]) / max(1, len(vols) - 1))
            if abs(gap) >= gap_trigger:
                triggered.append({**r, "gap_pct": round(gap, 1),
                                  "vol_ratio": round(vol_ratio, 1)})
        except Exception:
            continue
    return triggered


# ── Part B: AI 研判 ───────────────────────────────────────────────────────────
def analyze_earnings(symbol: str, gap_pct: float | None = None,
                     vol_ratio: float | None = None,
                     earnings_date: str | None = None, session: str | None = None) -> dict:
    """组装财报数据 + 历史反应 + 是否持有 → Claude 研判。人工决策,不下单。"""
    from src.config import get_anthropic_client
    holdings = _portfolio_symbols()
    held = symbol in holdings
    hist = historical_reactions(symbol, n=4)
    # 最近一次 EPS 超预期(若 yfinance 已更新)
    surprise = hist[0]["surprise_pct"] if hist else None

    hist_txt = "; ".join(
        f"{h['date']}: EPS超预期{h['surprise_pct']}% 后涨跌{h['reaction_pct']}%"
        for h in hist if h.get("reaction_pct") is not None) or "无历史数据"

    mode = "持仓建议(继续持有/减仓/清仓)" if held else "入场研判(值得关注/观望)"
    prompt = f"""你是短线交易研判助手。{symbol} 刚发财报,给出{mode}。

数据:
- 是否持有: {'是' if held else '否'}
- 盘后/隔夜跳空: {gap_pct}%
- 量比: {vol_ratio}
- 最近一次 EPS 超预期: {surprise}%
- 历史财报后表现: {hist_txt}

只输出 JSON: {{"summary": "一句话财报好坏+市场反应", "verdict": "{'持有/减仓/清仓' if held else '值得关注/观望'}", "confidence": 1-10, "reason": "理由,含追高/支撑提示"}}"""

    try:
        msg = get_anthropic_client().messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=400,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = msg.content[0].text.strip()
        # 容错:剥 ```json 包裹
        if "```" in raw:
            raw = raw.split("```")[1].lstrip("json").strip()
        data = json.loads(raw)
    except Exception as e:
        data = {"summary": "AI 研判失败", "verdict": "观望",
                "confidence": 0, "reason": f"解析错误: {e}"}

    result = {
        "symbol": symbol, "held": held, "gap_pct": gap_pct,
        "vol_ratio": vol_ratio, "surprise_pct": surprise,
        "history": hist, "analysis": data,
        "earnings_date": earnings_date, "session": session,
        "analyzed_at": datetime.now(timezone.utc).isoformat(),
    }
    return result


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "hist":
        print(json.dumps(historical_reactions(sys.argv[2]), indent=2, ensure_ascii=False))
    else:
        print("building calendar (small test = first 20 universe)…")
