#!/usr/bin/env python3
"""独立调度器看门狗 — 由 LaunchAgent 每 5 分钟调用一次,完全独立于后端进程。

背景(2026-06-24 实证):后端进程活着、HTTP 还能服务,但 APScheduler 调度器
整天零执行(疑似无超时调用占满线程池 → 冻结)。光靠后端自己发现不了 —— 必须有
一个外部进程盯着。

逻辑:
- 仅在市场时段(9:00–16:30 ET, Mon–Fri)生效(非市场时段心跳本就该静默)。
- 读 data/scheduler_heartbeat.json:每个调度任务执行后由 main.py 更新。
- 市场时段内最密的任务是每 5 分钟的 fill_sync → 正常心跳间隔 ≤5 分钟。
  若心跳 >35 分钟未更新 → 判定调度器冻结/死亡。
- 处理:kill :8000 监听进程(KeepAlive LaunchAgent 会重建)+ 写告警日志 + 桌面通知。
- 15 分钟冷却,防止重启循环。
"""
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
HB = ROOT / "data" / "scheduler_heartbeat.json"
ALERTS = ROOT / "data" / "watchdog_alerts.log"
COOLDOWN = ROOT / "data" / ".watchdog_last_restart"

STALE_MIN = 35
COOLDOWN_MIN = 15
ET = ZoneInfo("America/New_York")


def log_alert(msg: str) -> None:
    line = f"{datetime.now(ET):%Y-%m-%d %H:%M ET}  {msg}\n"
    try:
        with ALERTS.open("a") as f:
            f.write(line)
    except Exception:
        pass
    try:  # 桌面通知(best-effort)
        subprocess.run(
            ["osascript", "-e", f'display notification "{msg}" with title "交易系统看门狗"'],
            timeout=10, check=False,
        )
    except Exception:
        pass


def in_market_hours(now_et: datetime) -> bool:
    if now_et.weekday() >= 5:  # 周末
        return False
    open_t = now_et.replace(hour=9, minute=0, second=0, microsecond=0)
    close_t = now_et.replace(hour=16, minute=30, second=0, microsecond=0)
    return open_t <= now_et <= close_t


def heartbeat_age_min() -> float:
    """心跳距今多少分钟。文件缺失/损坏 → 视为极陈旧(999)。"""
    if not HB.exists():
        log_alert("⚠️ 心跳文件不存在 — 调度器可能从未启动")
        return 999.0
    try:
        hb = json.loads(HB.read_text())
        ts = datetime.fromisoformat(hb["ts_utc"]).replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - ts).total_seconds() / 60
    except Exception as e:
        log_alert(f"⚠️ 心跳文件无法解析: {e}")
        return 999.0


def in_cooldown() -> bool:
    if not COOLDOWN.exists():
        return False
    try:
        last = datetime.fromisoformat(COOLDOWN.read_text().strip()).replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - last).total_seconds() / 60 < COOLDOWN_MIN
    except Exception:
        return False


def restart_backend() -> None:
    COOLDOWN.write_text(datetime.now(timezone.utc).isoformat())
    try:
        pids = subprocess.check_output(
            ["lsof", "-ti:8000", "-sTCP:LISTEN"], text=True
        ).split()
        for pid in pids:
            subprocess.run(["kill", "-9", pid], timeout=10, check=False)
        log_alert(f"   已 kill PID {pids or '∅'} — 等 LaunchAgent(KeepAlive)重建调度器")
    except subprocess.CalledProcessError:
        log_alert("   :8000 无监听进程 — 等 LaunchAgent 拉起")
    except Exception as e:
        log_alert(f"   重启失败: {e}")


def main() -> None:
    now_et = datetime.now(ET)
    if not in_market_hours(now_et):
        return  # 非市场时段,心跳静默是正常的
    age = heartbeat_age_min()
    if age <= STALE_MIN:
        return  # 健康
    if in_cooldown():
        return  # 刚重启过,等冷却避免循环
    log_alert(f"🔴 调度器心跳已 {age:.0f} 分钟无更新(阈值 {STALE_MIN}) → 自动重启后端")
    restart_backend()


if __name__ == "__main__":
    main()
