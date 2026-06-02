"""Agent run-record tracking — Maya / Scout / Rex 运行状态聚合。

数据来源：
  - Maya  → data/market_context.json (generated_at)
  - Scout → data/dynamic_tickers.json (generated_at)
  - Rex   → trade_agent.get_agent_log()[0].run_at
手动/自动标记额外持久化到 data/agent_runs.json（由 record_agent_run 写入）。

调度时间是单一事实来源，必须与 main.py 的 APScheduler cron 保持一致。
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

_ROOT = Path(__file__).parent.parent
_RUNS_FILE = _ROOT / "data" / "agent_runs.json"
ET = ZoneInfo("America/New_York")

# ── 调度时间（ET）— 镜像 main.py 的 APScheduler ──────────────────────────────
AGENT_SCHEDULE: dict[str, dict] = {
    "maya": {
        "name": "Maya",
        "role": "市场环境分析（regime + 仓位激进度 + 板块偏好）",
        "times": ["08:00"],
        "cadence_note": "每日 8:00",
        "kind": "daily",
    },
    "scout": {
        "name": "Scout",
        "role": "选股：盘前动态发现（Finviz）+ 日内 S&P500 扫描 + AI 评分",
        "times": ["08:45", "09:31", "11:00", "12:30", "14:30"],
        "cadence_note": "盘前发现 8:45 + 4 次扫描",
        "kind": "intraday",
    },
    "rex": {
        "name": "Rex",
        "role": "交易执行：扫描后买入 + 持仓监控卖出（每 30 分钟）",
        "times": ["09:31", "11:00", "12:30", "14:30"],
        "cadence_note": "扫描后买入 + 每 30 分钟卖出",
        "kind": "intraday",
    },
}


_MAX_HISTORY = 60   # 每个 agent 最多保留最近 N 条（Rex 一个交易日 ~20 条，留几天余量）


def record_agent_run(agent: str, trigger: str = "auto",
                     result: str = "success", error: str | None = None) -> None:
    """追加一条 agent 运行记录到历史。

    Args:
        agent:   maya / scout / rex
        trigger: "auto"（调度任务）/ "manual"（手动 POST 端点）
        result:  "success" / "fail"
        error:   失败时的错误信息（截断保存）
    由各运行点的 try/except 调用：成功记 success，异常记 fail + error。
    """
    agent = agent.lower()
    if agent not in AGENT_SCHEDULE:
        return
    data = _load()
    hist = data.get(agent)
    if not isinstance(hist, list):   # 迁移旧的快照格式
        hist = []
    hist.append({
        "ran_at": datetime.now(timezone.utc).isoformat(),
        "trigger": trigger,
        "result": result,
        "error": (str(error)[:300] if error else None),
    })
    data[agent] = hist[-_MAX_HISTORY:]
    try:
        _RUNS_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    except Exception:
        pass


def _load() -> dict:
    if _RUNS_FILE.exists():
        try:
            return json.loads(_RUNS_FILE.read_text())
        except Exception:
            return {}
    return {}


def _parse_iso(iso: str | None) -> datetime | None:
    if not iso:
        return None
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        if dt.tzinfo is None:        # naive 时间戳（如 scan_cache.scanned_at）按 UTC 处理
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def _last_run_from_source(agent: str) -> str | None:
    """从各 agent 的真实输出文件读取最近运行时间戳。"""
    if agent == "maya":
        f = _ROOT / "data" / "market_context.json"
        if f.exists():
            try:
                return json.loads(f.read_text()).get("generated_at")
            except Exception:
                return None
    elif agent == "scout":
        # Scout 有两类产出：盘前 Finviz 发现 + 日内 S&P500 扫描。取较新者。
        candidates: list[str] = []
        f1 = _ROOT / "data" / "dynamic_tickers.json"
        if f1.exists():
            try:
                candidates.append(json.loads(f1.read_text()).get("generated_at"))
            except Exception:
                pass
        f2 = _ROOT / "data" / "scan_cache.json"
        if f2.exists():
            try:
                candidates.append(json.loads(f2.read_text()).get("sp500", {}).get("scanned_at"))
            except Exception:
                pass
        dated = [(d, c) for c in candidates if c and (d := _parse_iso(c))]
        if not dated:
            return None
        return max(dated, key=lambda x: x[0])[1]
    elif agent == "rex":
        try:
            from src.trader.trade_agent import get_agent_log
            log = get_agent_log()
            return log[0].get("run_at") if log else None
        except Exception:
            return None
    return None


def _age_str(dt: datetime | None, now: datetime) -> str | None:
    if not dt:
        return None
    mins = int((now - dt).total_seconds() / 60)
    if mins < 0:
        return "刚刚"
    if mins < 60:
        return f"{mins} 分钟前"
    if mins < 60 * 24:
        return f"{mins // 60} 小时前"
    return f"{mins // (60 * 24)} 天前"


def _health(agent_id: str, last_run: datetime | None, now_et: datetime) -> tuple[str, str]:
    """计算健康状态。返回 (status, label)。

    状态：
      ok        ✅ 今日已按计划运行
      waiting   ⏳ 今日尚未到运行时间
      missed    ⚠️ 已过运行时间但未见今日运行
      idle      ⚪ 非交易日（周末）
      never     ❌ 从未运行
    """
    sched = AGENT_SCHEDULE[agent_id]
    # 周末（周六=5，周日=6）非交易日
    if now_et.weekday() >= 5:
        return "idle", "⚪ 非交易日"
    if last_run is None:
        return "never", "❌ 从未运行"

    last_run_et = last_run.astimezone(ET)
    ran_today = last_run_et.date() == now_et.date()

    # 今日已过的计划运行时间
    passed: list[str] = []
    for t in sched["times"]:
        hh, mm = map(int, t.split(":"))
        sched_dt = now_et.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if now_et >= sched_dt:
            passed.append(t)

    if not passed:
        # 今日还没到任何计划时间
        if ran_today:
            return "ok", "✅ 今日已运行"
        return "waiting", "⏳ 待今日运行"

    # 已有计划时间到点
    if ran_today:
        return "ok", "✅ 正常运行"
    return "missed", f"⚠️ 已过 {passed[-1]} 未运行"


def get_agents_status() -> dict:
    """聚合 Maya / Scout / Rex 的运行历史 + 调度 + 健康检查。"""
    now_utc = datetime.now(timezone.utc)
    now_et = now_utc.astimezone(ET)
    store = _load()

    agents = []
    for agent_id, sched in AGENT_SCHEDULE.items():
        hist_raw = store.get(agent_id)
        if not isinstance(hist_raw, list):
            hist_raw = []

        # 运行历史（最新在前）
        history = []
        for e in reversed(hist_raw):
            dt = _parse_iso(e.get("ran_at"))
            history.append({
                "ran_at": e.get("ran_at"),
                "age": _age_str(dt, now_utc),
                "trigger": e.get("trigger"),
                "result": e.get("result"),
                "error": e.get("error"),
            })

        # 最近一条显式记录
        most_recent = hist_raw[-1] if hist_raw else None
        mr_dt = _parse_iso(most_recent["ran_at"]) if most_recent else None

        # 真实输出文件时间戳（用于回填本功能上线前/重启后的健康判断）
        src_iso = _last_run_from_source(agent_id)
        src_dt = _parse_iso(src_iso)

        # 健康判断用「显式记录」与「文件时间戳」较新者
        if most_recent and (not src_dt or (mr_dt and mr_dt >= src_dt)):
            last_dt, last_iso = mr_dt, most_recent["ran_at"]
            trigger, result = most_recent.get("trigger"), most_recent.get("result")
        elif src_dt:
            last_dt, last_iso = src_dt, src_iso
            trigger, result = "auto", None   # 文件时间戳无法确定来源/结果，按调度+未知处理
        else:
            last_dt, last_iso, trigger, result = None, None, None, None

        status, label = _health(agent_id, last_dt, now_et)
        agents.append({
            "id": agent_id,
            "name": sched["name"],
            "role": sched["role"],
            "scheduled_times_et": sched["times"],
            "cadence_note": sched.get("cadence_note"),
            "kind": sched["kind"],
            "last_run_at": last_iso,
            "age": _age_str(last_dt, now_utc),
            "trigger": trigger,            # "manual" | "auto" | None
            "result": result,             # "success" | "fail" | None
            "status": status,             # 健康：ok/waiting/missed/idle/never
            "status_label": label,
            "history": history,           # 全部（前端按「最近交易日 ET」过滤显示）
        })

    return {
        "now_et": now_et.isoformat(),
        "is_trading_day": now_et.weekday() < 5,
        "agents": agents,
    }
