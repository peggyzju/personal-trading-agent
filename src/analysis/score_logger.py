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
