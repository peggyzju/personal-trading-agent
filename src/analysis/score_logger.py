"""逐候选评分埋点 — 记录每次扫描里每个被 AI 打分的候选,供「AI 选股有没有 edge」分析。

- append-only `data/score_log.jsonl`,每行一个候选。
- 前向收益(fwd_5d/10d/20d)留空,由后续 fill 任务回填(Phase 2)。
- 调用方需 try/except 包裹,绝不影响扫描主流程。
"""
import json
from datetime import datetime, timezone
from pathlib import Path

try:
    from zoneinfo import ZoneInfo
    _ET = ZoneInfo("America/New_York")
except Exception:  # pragma: no cover
    _ET = None

_LOG_PATH = Path(__file__).parent.parent.parent / "data" / "score_log.jsonl"

# 入场技术快照(分析 edge 时可分层/控制变量)；缺失字段记 None
_TECH_FIELDS = [
    "price", "rsi", "momentum_5d", "momentum_1m", "momentum_3m",
    "volume_ratio", "vs_ma20_pct", "ma20_slope_pct", "sector",
    "today_bull", "near_breakout", "tech_score",
]


def record_scored_candidates(candidates, regime=None, min_ai_score=None):
    """把本次扫描所有「被 AI 打过分」的候选各写一行到 score_log.jsonl。返回写入条数。"""
    if not candidates:
        return 0
    now = datetime.now(timezone.utc)
    scan_date = now.astimezone(_ET).strftime("%Y-%m-%d") if _ET else now.strftime("%Y-%m-%d")

    rows = []
    for c in candidates:
        if c.get("ai_score") is None:   # 只记真正被 AI 评分的(排除 fallback 的 None)
            continue
        row = {
            "logged_at": now.isoformat(),
            "scan_date": scan_date,
            "symbol": c.get("symbol"),
            "ai_score": c.get("ai_score"),
            "signal": c.get("signal"),
            "regime": regime,
            "min_ai_score": min_ai_score,
            "screen_track": c.get("screen_track") or c.get("track"),
            "fwd_5d": None, "fwd_10d": None, "fwd_20d": None, "fwd_filled_at": None,
        }
        for f in _TECH_FIELDS:
            row[f] = c.get(f)
        rows.append(row)

    if not rows:
        return 0
    _LOG_PATH.parent.mkdir(exist_ok=True)
    with _LOG_PATH.open("a") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    return len(rows)


# ── Phase 2: 前向收益回填 ──────────────────────────────────────────────────────
_HORIZONS = [("fwd_5d", 5), ("fwd_10d", 10), ("fwd_20d", 20)]


def _read_log():
    if not _LOG_PATH.exists():
        return []
    out = []
    for line in _LOG_PATH.read_text().splitlines():
        line = line.strip()
        if line:
            try:
                out.append(json.loads(line))
            except Exception:
                pass
    return out


def fill_forward_returns():
    """回填到期的前向收益(5/10/20 交易日,从 scan_date 收盘起算)。返回填充的字段数。

    用 Alpaca 日线(带日期 index)。只填「已过足够交易日」的；不够的留 None,下次再填。
    无 look-ahead(只用 scan_date 之后的价)。调用方应 try/except。
    """
    import datetime as _dt
    rows = _read_log()
    if not rows:
        return 0
    pending = [r for r in rows if r.get("symbol") and r.get("scan_date")
               and any(r.get(k) is None for k, _ in _HORIZONS)]
    if not pending:
        return 0

    symbols = sorted({r["symbol"] for r in pending})
    earliest = min(r["scan_date"] for r in pending)
    today = _dt.date.today()
    days_back = (today - _dt.date.fromisoformat(earliest)).days + 45

    try:
        from src.trader.alpaca_trader import get_client
        api = get_client()
        start = (today - _dt.timedelta(days=days_back)).isoformat()
        df = api.get_bars(symbols, "1Day", start=start, end=today.isoformat(), feed="iex").df
    except Exception as e:
        print(f"[score_log] forward-fill 拉数失败: {e}")
        return 0
    if df is None or df.empty:
        return 0

    has_sym = "symbol" in df.columns
    per = {}   # symbol -> (sorted trading dates, {date: close})
    for sym in symbols:
        sub = df[df["symbol"] == sym] if has_sym else df
        if sub.empty:
            continue
        dates = [d.date().isoformat() if hasattr(d, "date") else str(d)[:10] for d in sub.index]
        closes = [float(c) for c in sub["close"].values]
        per[sym] = (dates, dict(zip(dates, closes)))

    now_iso = _dt.datetime.now(_dt.timezone.utc).isoformat()
    filled = 0
    for r in pending:
        info = per.get(r["symbol"])
        if not info:
            continue
        dates, dmap = info
        base_i = next((i for i, d in enumerate(dates) if d >= r["scan_date"]), None)
        if base_i is None:
            continue
        base_close = dmap[dates[base_i]]
        if not base_close:
            continue
        for key, n in _HORIZONS:
            if r.get(key) is not None:
                continue
            j = base_i + n
            if j < len(dates) and dmap[dates[j]]:
                r[key] = round((dmap[dates[j]] - base_close) / base_close * 100, 2)
                r["fwd_filled_at"] = now_iso
                filled += 1

    if filled:
        with _LOG_PATH.open("w") as fh:
            for r in rows:
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    return filled
