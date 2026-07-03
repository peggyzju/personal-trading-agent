"""
Personal Trading Agent
Run: python3 main.py
  API:  http://localhost:8000
  Docs: http://localhost:8000/docs
"""
from __future__ import annotations
import os
import json
import socket
from datetime import datetime
from pathlib import Path
import uvicorn
from dotenv import load_dotenv
from apscheduler.schedulers.background import BackgroundScheduler

load_dotenv()

# 全网络读硬超时:任何 Alpaca / Anthropic / yfinance 调用最多挂 90 秒。
# 杜绝"无超时调用挂死 → 占满 APScheduler 线程池 → 整个调度器冻结"
# (2026-06-24 实证:进程活、HTTP 通,但调度器整天零执行)。
socket.setdefaulttimeout(90)


_HEARTBEAT_FILE = Path(__file__).parent / "data" / "scheduler_heartbeat.json"


def _write_heartbeat(job_id: str, status: str) -> None:
    """每个调度任务执行后写心跳 — 独立看门狗(scheduler_watchdog.py)据此
    判断调度器是否冻结。调度器一旦卡死,心跳就停更 → 看门狗重启后端。"""
    try:
        _HEARTBEAT_FILE.write_text(json.dumps({
            "last_job": job_id,
            "status": status,
            "ts_utc": datetime.utcnow().isoformat(),
        }))
    except Exception:
        pass


def _is_real_trading_day() -> bool:
    from src.trader.market_calendar import is_trading_day_et

    return is_trading_day_et()


def _skip_non_trading_day(job_id: str) -> bool:
    if _is_real_trading_day():
        return False
    from src.trader.market_calendar import now_et

    print(f"[scheduler] {job_id} skipped — {now_et():%Y-%m-%d} ET is not an Alpaca trading session")
    return True

WATCHLIST_DEFAULT = ["AAPL", "NVDA", "MSFT", "TSLA"]


ANALYSIS_MAX_AGE_HOURS = 2    # re-analyze if cache older than this
ANALYSIS_MOVE_THRESHOLD = 1.5  # re-analyze if price moved more than this %


def run_analysis_cycle():
    if _skip_non_trading_day("analysis_cycle"):
        return
    import json
    import time as _t
    from pathlib import Path
    from src.monitor.price_monitor import get_quote, get_ohlcv
    from src.monitor.news_monitor import get_news
    from src.analysis.ai_analyst import analyze
    from src.alerts.notifier import console_alert
    from api.app import _analysis_cache, _analysis_timestamps

    wl_file = Path("watchlist.json")
    watchlist = json.loads(wl_file.read_text()) if wl_file.exists() else WATCHLIST_DEFAULT

    print(f"\n--- Analysis cycle: {watchlist} ---")
    now = _t.time()
    for symbol in watchlist:
        try:
            quote = get_quote(symbol)
            current_price = quote["price"]

            # Skip if cache is fresh AND price hasn't moved significantly
            last_ts = _analysis_timestamps.get(symbol, 0)
            last_price = (_analysis_cache.get(symbol) or {}).get("price", 0)
            age_hours = (now - last_ts) / 3600
            price_move = abs(current_price - last_price) / last_price * 100 if last_price else 999

            if age_hours < ANALYSIS_MAX_AGE_HOURS and price_move < ANALYSIS_MOVE_THRESHOLD:
                print(f"  {symbol}: skip (age={age_hours:.1f}h, move={price_move:.2f}%)")
                continue

            ohlcv = get_ohlcv(symbol)
            news = get_news(symbol)
            result = analyze(symbol, ohlcv, quote, news=news)
            result.update({"symbol": symbol, "price": current_price, "change_pct": quote["change_pct"]})
            _analysis_cache[symbol] = result
            _analysis_timestamps[symbol] = now

            console_alert(symbol, result.get("signal", "HOLD"), current_price, result.get("reasoning", ""))
        except Exception as e:
            print(f"  [ERROR] {symbol}: {e}")


def run_scout():
    """Pre-market dynamic ticker discovery via Finviz (9:00 AM ET Mon–Fri).
    Scout caches results for the day; calling it again is a no-op if already ran.
    """
    if _skip_non_trading_day("scout"):
        return
    print("[scheduler] Running pre-market Scout discovery…")
    from api.agent_runs import record_agent_run
    try:
        from src.monitor.scout import run as scout_run
        tickers = scout_run()
        record_agent_run("scout", trigger="auto", result="success")
        print(f"[scheduler] Scout done: {len(tickers)} dynamic tickers discovered")
    except Exception as e:
        record_agent_run("scout", trigger="auto", result="fail", error=str(e))
        print(f"[scheduler] Scout error: {e}")


def run_market_context():
    """Step 1 of pipeline — generate market context (regime + goal progress + sector bias).
    Runs at 8:00 AM ET, before scan and agent."""
    if _skip_non_trading_day("market_context"):
        return
    from src.analysis.market_context import generate_market_context
    from api.agent_runs import record_agent_run
    print("[scheduler] Generating market context…")
    try:
        ctx = generate_market_context()
        record_agent_run("maya", trigger="auto", result="success")
    except Exception as e:
        record_agent_run("maya", trigger="auto", result="fail", error=str(e))
        print(f"[scheduler] Market context error: {e}")
        return
    gc  = ctx.get("goal_context", {})
    print(
        f"[scheduler] Context: regime={ctx['regime']} aggression={ctx['aggression']} "
        f"min_score={ctx['min_ai_score']} "
        f"day={gc.get('days_elapsed')}/{gc.get('days_elapsed',0)+gc.get('days_remaining',0)-1} "
        f"return={gc.get('current_return_pct',0):+.2f}% "
        f"need/day={gc.get('daily_return_needed',0):.2f}%"
    )
    # 财报雷达:每天生成未来 7 天全市场财报日历(非致命,失败不影响 Maya)
    try:
        from src.monitor.earnings_radar import build_calendar
        cal = build_calendar(days=7)
        print(f"[scheduler] 财报日历: 未来7天 {cal['count']} 只发财报 "
              f"(持仓 {cal['holdings_reporting']} 只)")
    except Exception as e:
        print(f"[scheduler] 财报日历生成失败(不影响 Maya): {e}")


def _desktop_notify(title: str, msg: str) -> None:
    """桌面通知(best-effort,白天有用;后半夜 AMC 财报用户看不到,见设计文档)。"""
    try:
        import subprocess
        subprocess.run(["osascript", "-e",
                        f'display notification "{msg}" with title "{title}"'],
                       timeout=10, check=False)
    except Exception:
        pass


def run_earnings_scan():
    """财报雷达 Part B:检测当日财报名单的显著价格反应 → AI 研判 → 桌面通知。
    人工决策,不自动下单。结果写 data/earnings_analysis.json。"""
    if _skip_non_trading_day("earnings_scan"):
        return
    import json as _json
    from datetime import datetime as _dt, timezone as _tz, timedelta as _td
    print("[scheduler] 财报反应扫描…")
    try:
        from src.monitor.earnings_radar import detect_reactions, analyze_earnings, reaction_ready, _ANALYSIS_FILE
        # 载入现有,剪掉:① 已不是"财报后跳空可测"(如当日盘后还没出的票)② 3天前的旧研判
        existing = {}
        if _ANALYSIS_FILE.exists():
            try:
                _cut = (_dt.now(_tz.utc) - _td(days=3)).isoformat()
                existing = {a["symbol"]: a for a in _json.loads(_ANALYSIS_FILE.read_text())
                            if reaction_ready(a.get("earnings_date"), a.get("session"))
                            and (a.get("analyzed_at") or "") >= _cut}
            except Exception:
                existing = {}
        for t in detect_reactions():
            sym = t["symbol"]
            res = analyze_earnings(sym, gap_pct=t.get("gap_pct"), vol_ratio=t.get("vol_ratio"),
                                   earnings_date=t.get("date"), session=t.get("session"))
            existing[sym] = res
            a = res.get("analysis", {})
            _desktop_notify(f"财报研判 {sym} {t.get('gap_pct'):+}%",
                            f"{a.get('verdict','')} · {a.get('summary','')[:60]}")
            print(f"[scheduler] 财报研判 {sym}: {a.get('verdict')} (跳空 {t.get('gap_pct')}%)")
        # 即使本次无新触发也回写(让剪枝生效,自清理陈旧研判)
        _ANALYSIS_FILE.write_text(_json.dumps(list(existing.values()), indent=2, ensure_ascii=False))
    except Exception as e:
        print(f"[scheduler] 财报反应扫描失败: {e}")


def run_sp500_scan():
    """Step 2 of pipeline — scan S&P 500, then auto-cascade to agent."""
    if _skip_non_trading_day("sp500_scan"):
        return
    from api.app import _run_sp500_scan
    print("[scheduler] Running daily S&P 500 scan (cascade→agent)…")
    try:
        # scout 运行记录已收口到 _run_sp500_scan 内部(完成即记一次),此处只触发。
        status = _run_sp500_scan(cascade_agent=True, trigger="auto")
        if status == "skipped":
            print("[scheduler] scan 被跳过（已在运行）— 不记录运行历史，避免假成功")
        elif status != "done":
            print(f"[scheduler] S&P 500 scan {status}")
    except Exception as e:
        print(f"[scheduler] S&P 500 scan error: {e}")


def run_holdings_refresh():
    """Refresh paper holdings and sell signals every 30 min during market hours.
    After analysis completes, cascade to Rex so sell signals are acted on immediately.
    """
    if _skip_non_trading_day("holdings_refresh"):
        return
    from src.monitor.holdings_monitor import get_paper_positions, analyze_sell_signals
    from api.app import _holdings_cache
    print("[scheduler] Refreshing holdings & sell signals…")
    positions = get_paper_positions()
    _holdings_cache["positions"] = positions
    try:
        enriched = analyze_sell_signals(positions)
        _holdings_cache["positions"] = enriched
        _holdings_cache["analyzed"] = True
    except Exception as e:
        print(f"[scheduler] holdings error: {e}")
        return

    # ── Cascade to Rex (sell-signal execution) — 仅开盘后（≥9:31 ET）──────────────
    # 盘前（如 9:00）价格不实时，AI 卖出会基于陈旧价误判 + 开盘跳空成交；主止损由
    # Alpaca 服务端 bracket 兜底，不依赖盘前轮询。开盘后首次卖出检查由 9:31 扫描
    # cascade 覆盖，不会断档。持仓缓存刷新（上方）保留，UI 盘前仍可看持仓。
    from datetime import datetime as _dt
    from zoneinfo import ZoneInfo as _ZI
    _et = _dt.now(_ZI("America/New_York"))
    from src.trader.market_calendar import is_market_hours_et
    _after_open = is_market_hours_et(_et)
    if positions and not _after_open:
        print(f"[scheduler] Holdings 缓存已刷新（{_et:%H:%M} ET 盘前/非交易时段，暂不 cascade 卖出）")
    elif positions:
        print("[scheduler] Holdings refresh done → cascading to Rex (sell signals)…")
        from api.agent_runs import record_agent_run
        try:
            from api.app import _run_agent_internal
            summary = _run_agent_internal()
            st = (summary or {}).get("status")
            if st == "already_running":
                print("[scheduler] Rex cascade skipped (already running) — 不记录")
            elif st == "error":
                record_agent_run("rex", trigger="auto", result="fail", error=(summary or {}).get("error"))
            else:
                record_agent_run("rex", trigger="auto", result="success")
        except Exception as e:
            record_agent_run("rex", trigger="auto", result="fail", error=str(e))
            print(f"[scheduler] Rex cascade error: {e}")


def sync_order_fills():
    """Poll Alpaca every 5 min during market hours for fill status updates."""
    if _skip_non_trading_day("fill_sync"):
        return
    from src.trader.trade_agent import sync_fills
    changed = sync_fills()
    if changed:
        print(f"[scheduler] Fill sync: {len(changed)} order(s) updated")


def sync_trade_history():
    """每个交易日收盘后从 Alpaca 回填已平仓交易到 trade_history.json（幂等，无交易副作用）。
    绩效统计(胜率/盈亏比)的数据源就是它——不挂这个定时任务，平仓数据会一直是死数据。"""
    if _skip_non_trading_day("trade_history_sync"):
        return
    try:
        from src.analysis.strategy_versions import sync_closed_trades_from_alpaca
        from src.trader.alpaca_trader import get_client
        added = sync_closed_trades_from_alpaca(get_client(), days=30)
        print(f"[scheduler] Trade history sync: {added} closed trade(s) recorded")
    except Exception as e:
        print(f"[scheduler] Trade history sync error: {e}")


def fill_score_forward_returns():
    """每交易日收盘后回填 score_log 候选的前向收益(5/10/20 交易日)。
    AI-edge 分析的数据源；只填已到期的,无 look-ahead。"""
    if _skip_non_trading_day("score_fwd_fill"):
        return
    try:
        from src.analysis.score_logger import fill_forward_returns
        n = fill_forward_returns()
        if n:
            print(f"[scheduler] Score-log forward fill: {n} field(s) filled")
    except Exception as e:
        print(f"[scheduler] Score-log forward fill error: {e}")


def catch_up_premarket():
    """盘前补跑看门狗(9:00 ET)：若 Maya/Scout 今天还没成功跑过 → 手动补跑。
    应对睡眠漏跑：本任务给长 misfire 容差(90 分钟),机器睡到 9 点后才醒也能补
    (普通任务 misfire=60s 错过即跳过,补不了)。幂等:已跑过则跳过。"""
    if _skip_non_trading_day("premarket_catchup"):
        return
    from api.agent_runs import ran_today_et
    if not ran_today_et("maya"):
        print("[scheduler] 盘前补跑看门狗: Maya 今天未跑 → 补跑 8:00 Maya")
        run_market_context()
    else:
        print("[scheduler] 盘前补跑看门狗: Maya 今天已跑,跳过")
    if not ran_today_et("scout"):
        print("[scheduler] 盘前补跑看门狗: Scout 今天未跑 → 补跑 8:45 Scout")
        run_scout()
    else:
        print("[scheduler] 盘前补跑看门狗: Scout 今天已跑,跳过")


if __name__ == "__main__":
    print("📎 Personal Trading Agent")
    print("   API: http://localhost:8000  |  Docs: http://localhost:8000/docs\n")

    # All cron times are US/Eastern — explicit timezone avoids DST/UTC confusion
    ET = "America/New_York"
    # misfire_grace_time=60: 重启后若错过触发时间 ≤60s 则补跑，超过则跳过（避免任务雪崩）
    MGT = 60

    # job_defaults: max_instances=1 防同一任务堆叠多个冻结实例;coalesce 合并错过的触发
    scheduler = BackgroundScheduler(
        timezone=ET,
        job_defaults={"max_instances": 1, "coalesce": True},
    )

    # 心跳:任意任务执行完(成功或异常)都更新 scheduler_heartbeat.json。
    # 独立看门狗据此检测调度器冻结(市场时段内心跳停更 >35 分钟 → 自动重启)。
    from apscheduler.events import EVENT_JOB_EXECUTED, EVENT_JOB_ERROR
    scheduler.add_listener(
        lambda ev: _write_heartbeat(ev.job_id, "error" if ev.exception else "ok"),
        EVENT_JOB_EXECUTED | EVENT_JOB_ERROR,
    )

    # ── Pipeline: 市场分析 → 选股 → 执行 ─────────────────────────────────────
    #
    #  8:00 AM  Market context (regime + goal progress + sector bias)
    #  8:45 AM  Maya/Scout pre-market dynamic discovery
    #  9:31 AM  扫描第1次 → cascade → Rex (buy signals)
    # 11:00 AM  扫描第2次 → cascade → Rex (buy signals)
    # 12:30 PM  扫描第3次 → cascade → Rex (buy signals)
    # 14:30 PM  扫描第4次 → cascade → Rex (buy signals)
    #  every 30 min  Holdings refresh → cascade → Rex (sell signals only)
    #  every 5  min  Fill sync (order status)
    #  Vera 复盘：已移除自动定时（数据太少噪音大），改为手动 trigger
    #            手动入口：POST /api/strategy/review（前端「复盘」Tab 按钮）
    # ──────────────────────────────────────────────────────────────────────────

    # Step 1: Market context
    scheduler.add_job(run_market_context, "cron", day_of_week="mon-fri", hour=8, minute=0,
                      id="market_context", name="Market context (8:00 AM ET)",
                      misfire_grace_time=MGT)

    # Step 0 (pre-market): Maya/Scout dynamic ticker discovery
    scheduler.add_job(run_scout, "cron", day_of_week="mon-fri", hour=8, minute=45,
                      id="scout", name="Maya/Scout pre-market discovery (8:45 AM ET)",
                      misfire_grace_time=MGT)

    # Step 2: S&P 500 scan × 4 — cascade_agent=True → auto-triggers Rex on completion
    scheduler.add_job(run_sp500_scan, "cron", day_of_week="mon-fri", hour=9, minute=31,
                      id="scan_0931", name="扫描第1次 + Rex cascade (9:31 AM ET)",
                      misfire_grace_time=MGT)
    scheduler.add_job(run_sp500_scan, "cron", day_of_week="mon-fri", hour=11, minute=0,
                      id="scan_1100", name="扫描第2次 + Rex cascade (11:00 AM ET)",
                      misfire_grace_time=MGT)
    scheduler.add_job(run_sp500_scan, "cron", day_of_week="mon-fri", hour=12, minute=30,
                      id="scan_1230", name="扫描第3次 + Rex cascade (12:30 PM ET)",
                      misfire_grace_time=MGT)
    scheduler.add_job(run_sp500_scan, "cron", day_of_week="mon-fri", hour=14, minute=30,
                      id="scan_1430", name="扫描第4次 + Rex cascade (2:30 PM ET)",
                      misfire_grace_time=MGT)

    # Step 3: Holdings refresh every 30 min → cascades to Rex for sell execution
    scheduler.add_job(run_holdings_refresh, "cron", day_of_week="mon-fri", hour="9-15", minute="*/30",
                      id="holdings_refresh", name="Holdings refresh + Rex sell cascade (every 30 min)",
                      misfire_grace_time=MGT)

    # Watchlist analysis cycle: every 30 min (independent of Rex)
    scheduler.add_job(run_analysis_cycle, "cron", day_of_week="mon-fri", hour="9-15", minute="*/30",
                      id="analysis_cycle", name="Watchlist analysis cycle (every 30 min)",
                      misfire_grace_time=MGT)

    # Order fill sync: every 5 min during market hours
    scheduler.add_job(sync_order_fills, "cron", day_of_week="mon-fri", hour="9-16", minute="*/5",
                      id="fill_sync", name="Order fill sync (every 5 min)",
                      misfire_grace_time=MGT)

    # Trade history sync: 每个交易日收盘后回填平仓单 → 绩效统计不再变死数据
    scheduler.add_job(sync_trade_history, "cron", day_of_week="mon-fri", hour=16, minute=10,
                      id="trade_history_sync", name="Trade history sync (4:10 PM ET, 收盘后)",
                      misfire_grace_time=300)

    # Score-log 前向收益回填: 收盘后(trade_history 同步之后) → AI-edge 分析数据源
    scheduler.add_job(fill_score_forward_returns, "cron", day_of_week="mon-fri", hour=16, minute=20,
                      id="score_fwd_fill", name="Score-log forward-return fill (4:20 PM ET, 收盘后)",
                      misfire_grace_time=300)

    # 盘前补跑看门狗: 9:00 ET 检查 Maya/Scout 今天跑没跑,没跑就补。
    # 长 misfire(90分钟)→ 机器睡到 ~10:30 前醒来都能补上(应对睡眠漏跑)
    scheduler.add_job(catch_up_premarket, "cron", day_of_week="mon-fri", hour=9, minute=0,
                      id="premarket_catchup", name="盘前补跑看门狗 (9:00 ET, Maya/Scout)",
                      misfire_grace_time=5400)

    # 财报雷达 Part B: 每 15 分钟检测当日财报名单的价格反应 → AI 研判 + 桌面通知。
    # 覆盖盘前(BMO)+盘后(AMC)反应窗口 7:00–20:00 ET。人工决策,不下单。
    scheduler.add_job(run_earnings_scan, "cron", day_of_week="mon-fri", hour="7-20", minute="*/15",
                      id="earnings_scan", name="财报反应扫描 + AI研判 (7:00–20:00 ET, 每15分钟)",
                      misfire_grace_time=MGT)

    # Vera 复盘已移除自动定时 — 改为手动 trigger（POST /api/strategy/review）

    scheduler.start()
    _write_heartbeat("startup", "ok")
    print("[scheduler] Started (single source of truth — APScheduler, US/Eastern)")
    print("  8:45 AM Maya/Scout | 9:31/11:00/12:30/14:30 扫描→Rex | every 30min holdings→Rex")
    print("  Rex买入: 仅扫描后触发 (4次/天)  |  Rex卖出: 持仓监控后触发 (每30分钟)")
    print("  Vera 复盘: 已移除自动定时，改手动 trigger (POST /api/strategy/review)\n")

    from api.app import app
    uvicorn.run(app, host="0.0.0.0", port=8000)
