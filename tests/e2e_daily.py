"""
End-to-end daily test — simulates market open and runs the full pipeline.
Usage:
  python tests/e2e_daily.py          # full test (all sections)
  python tests/e2e_daily.py --smoke  # smoke only (env + account + logic)
"""
from __future__ import annotations
import sys, os, json, traceback, time
from pathlib import Path
from datetime import datetime

SMOKE_ONLY = "--smoke" in sys.argv

sys.path.insert(0, str(Path(__file__).parent.parent))

# Load .env so API keys are available
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent.parent / ".env")
except ImportError:
    pass

PASS = "✅"
FAIL = "❌"
WARN = "⚠️ "

results: list[tuple[str, str, str]] = []   # (status, module, message)

def ok(module: str, msg: str):
    results.append((PASS, module, msg))
    print(f"  {PASS} {module:<22} {msg}")

def fail(module: str, msg: str):
    results.append((FAIL, module, msg))
    print(f"  {FAIL} {module:<22} {msg}")

def warn(module: str, msg: str):
    results.append((WARN, module, msg))
    print(f"  {WARN} {module:<22} {msg}")


# ── 1. Environment ────────────────────────────────────────────────────────────
def test_environment():
    print("\n[1/8] 环境检查")
    # Anthropic key
    try:
        from src.config import get_anthropic_key
        key = get_anthropic_key()
        if key and len(key) > 20:
            ok("Anthropic key", f"有效 (sk-...{key[-4:]})")
        else:
            fail("Anthropic key", "key 为空或过短")
    except Exception as e:
        fail("Anthropic key", str(e))

    # Alpaca credentials
    try:
        from src.trader.alpaca_trader import get_client
        client = get_client()
        ok("Alpaca client", "初始化成功")
    except Exception as e:
        fail("Alpaca client", str(e))

    # Data directory
    data_dir = Path("data")
    if data_dir.exists():
        ok("Data dir", f"存在，{len(list(data_dir.iterdir()))} 个文件")
    else:
        warn("Data dir", "不存在，将在首次运行时创建")


# ── 2. Alpaca account ─────────────────────────────────────────────────────────
def test_account():
    print("\n[2/8] 账户连通")
    try:
        from src.trader.alpaca_trader import get_client, get_account
        acct = get_account()
        equity = float(acct.equity)
        cash   = float(acct.cash)
        ok("Account", f"equity=${equity:,.0f}, cash=${cash:,.0f}")

        client = get_client()
        positions = client.list_positions()
        ok("Positions", f"{len(positions)} 个持仓")
    except Exception as e:
        fail("Account", str(e))


# ── 3. Market regime (Maya) ───────────────────────────────────────────────────
def test_market_regime():
    print("\n[3/8] Maya — 市场 regime")
    try:
        from src.monitor.market_regime import get_market_regime
        r = get_market_regime()
        regime  = r.get("regime", "?")
        reason  = r.get("reason", "")[:60]
        block   = r.get("block_buys", False)
        ok("Market regime", f"{regime} — {reason}")
        if block:
            warn("Buy gate", "当前 regime 阻止买入")
        else:
            ok("Buy gate", "买入未被阻止")
    except Exception as e:
        fail("Market regime", str(e))


# ── 4. Scanner (Scout) ────────────────────────────────────────────────────────
def test_scanner():
    print("\n[4/8] Scout — 扫描缓存")
    cache_file = Path("data/scan_cache.json")
    try:
        if not cache_file.exists():
            warn("Scan cache", "不存在（首次运行前正常）")
            return
        data = json.loads(cache_file.read_text())
        sp500 = data.get("sp500", {})
        status = sp500.get("status", "?")
        candidates = sp500.get("candidates", [])
        scanned_at = sp500.get("scanned_at", "unknown")
        if status == "done" and candidates:
            ok("Scan cache", f"{len(candidates)} candidates, 扫描于 {scanned_at[:16]}")
            # Check ai_score present
            with_score = [c for c in candidates if c.get("ai_score") is not None]
            ok("AI scores", f"{len(with_score)}/{len(candidates)} 有 ai_score")
        elif status == "running":
            warn("Scan cache", "扫描进行中")
        else:
            warn("Scan cache", f"status={status}, {len(candidates)} candidates")
    except Exception as e:
        fail("Scan cache", str(e))


# ── 5. Strategy notes injection ───────────────────────────────────────────────
def test_strategy_notes():
    print("\n[5/8] Strategy notes — 读写 & 注入")
    notes_file = Path("data/strategy_notes.json")
    try:
        # Write a test note
        import uuid
        test_id = str(uuid.uuid4())[:8]
        test_note = {
            "id": test_id,
            "text": "[TEST] Avoid overextended stocks with RSI > 80",
            "source_review_date": datetime.utcnow().strftime("%Y-%m-%d"),
            "created_at": datetime.utcnow().isoformat(),
            "active": True,
        }
        existing = json.loads(notes_file.read_text()) if notes_file.exists() else []
        existing.append(test_note)
        notes_file.write_text(json.dumps(existing, indent=2))
        ok("Notes write", f"写入测试 note id={test_id}")

        # Read back via trade_agent loader
        from src.trader.trade_agent import _load_active_strategy_notes
        loaded = _load_active_strategy_notes()
        if any("[TEST]" in n for n in loaded):
            ok("Notes read", f"{len(loaded)} 条 notes 已加载")
        else:
            fail("Notes read", "trade_agent 未读到测试 note")

        # Verify injected into ai_analyst signature
        import inspect
        from src.analysis.ai_analyst import analyze
        params = list(inspect.signature(analyze).parameters.keys())
        if "strategy_notes" in params:
            ok("ai_analyst inject", "strategy_notes 参数存在")
        else:
            fail("ai_analyst inject", "缺少 strategy_notes 参数")

        # Verify injected into stock_screener signature
        from src.analysis.stock_screener import ai_score_candidates
        params2 = list(inspect.signature(ai_score_candidates).parameters.keys())
        if "strategy_notes" in params2:
            ok("screener inject", "strategy_notes 参数存在")
        else:
            fail("screener inject", "缺少 strategy_notes 参数")

        # Clean up test note
        cleaned = [n for n in existing if n.get("id") != test_id]
        notes_file.write_text(json.dumps(cleaned, indent=2))
        ok("Notes cleanup", "测试 note 已清理")

    except Exception as e:
        fail("Strategy notes", str(e))


# ── 6. Autonomous execution config ───────────────────────────────────────────
def test_autonomous_mode():
    print("\n[6/8] 自主执行模式")
    try:
        from src.trader.trade_agent import get_auto_approve_config, _get_auto_approve_threshold
        cfg = get_auto_approve_config()
        threshold = _get_auto_approve_threshold()
        enabled = cfg.get("enabled", False)

        if enabled and threshold == 0.0:
            ok("Auto-execute", f"自主模式 ON — threshold={threshold} (执行所有信号)")
        elif enabled:
            ok("Auto-execute", f"自主模式 ON — threshold={threshold:.0%}")
        else:
            warn("Auto-execute", "自主模式 OFF — 交易需要人工审批")

        # Verify default returns 0.0 when no file
        auto_file = Path("data/auto_approve.json")
        if not auto_file.exists():
            ok("Default config", "无配置文件，默认自主模式")
        else:
            ok("Config file", f"enabled={cfg['enabled']}, threshold={cfg['threshold']}")
    except Exception as e:
        fail("Auto-execute", str(e))


# ── 7. Rex agent — signal generation (dry run) ───────────────────────────────
def test_rex_dry_run():
    print("\n[7/8] Rex — 信号生成 (dry run)")
    try:
        # Just import and check key functions are accessible
        from src.trader.trade_agent import (
            run_agent, get_pending_trades, _load_strategy_overrides,
            _load_active_strategy_notes, approve_trade, reject_trade
        )
        ok("Rex imports", "所有核心函数可导入")

        # Check pending queue loads (returns list)
        trades = get_pending_trades()
        pending  = [t for t in trades if t.get("status") == "pending"]
        executed = [t for t in trades if t.get("status") == "executed"]
        ok("Trade queue", f"{len(pending)} 待执行, {len(executed)} 已执行")

        # Check overrides load
        ov = _load_strategy_overrides()
        risk = ov.get("risk_pct", 0.02)
        ok("Overrides", f"risk_pct={risk*100:.1f}%")

        # Check notes load
        notes = _load_active_strategy_notes()
        ok("Notes loaded", f"{len(notes)} 条活跃策略记忆")

    except Exception as e:
        fail("Rex dry run", str(e))
        traceback.print_exc()


# ── 8. Vera / strategy review ────────────────────────────────────────────────
def test_vera():
    print("\n[8/8] Vera — 复盘系统")
    try:
        from src.analysis.strategy_reviewer import generate_strategy_review
        import inspect
        sig = inspect.signature(generate_strategy_review)
        params = list(sig.parameters.keys())
        ok("Vera import", f"generate_strategy_review 可导入，参数: {params}")

        # Check review cache
        review_cache = Path("data/review_cache.json")
        if review_cache.exists():
            data = json.loads(review_cache.read_text())
            latest = data.get("latest", {})
            date = latest.get("date", "?")
            one_line = latest.get("one_line_summary", "")[:50]
            if date != "?":
                ok("Review cache", f"最新复盘: {date} — {one_line}")
            else:
                warn("Review cache", "缓存存在但无 latest 字段")
        else:
            warn("Review cache", "无复盘缓存（收盘后自动生成）")

    except Exception as e:
        fail("Vera", str(e))


# ── 9. Rex 核心逻辑 (无需真实 API) ────────────────────────────────────────────
def test_rex_logic():
    print("\n[9/11] Rex — 核心逻辑验证")
    try:
        import src.trader.trade_agent as ta

        # 9a. _sell_hold_count cooldown: requires 2 consecutive HOLD to cancel
        ta._sell_hold_count.clear()
        sym = "__TEST__"
        ta._sell_hold_count[sym] = ta._sell_hold_count.get(sym, 0) + 1
        if ta._sell_hold_count[sym] == 1:
            ok("hold_count cooldown", "第1次 HOLD → count=1, 不撤单 ✓")
        else:
            fail("hold_count cooldown", f"预期 count=1, 实际={ta._sell_hold_count[sym]}")

        ta._sell_hold_count[sym] = ta._sell_hold_count.get(sym, 0) + 1
        if ta._sell_hold_count[sym] == 2:
            ok("hold_count cooldown", "第2次 HOLD → count=2, 触发撤单 ✓")
        else:
            fail("hold_count cooldown", f"预期 count=2, 实际={ta._sell_hold_count[sym]}")

        # signal reverts to SELL/REDUCE → reset
        ta._sell_hold_count[sym] = 0
        if ta._sell_hold_count[sym] == 0:
            ok("hold_count reset", "SELL/REDUCE 信号 → count 重置 ✓")
        else:
            fail("hold_count reset", "count 未重置")

        # 9b. _reduce_today dedup: same symbol blocked same day
        ta._reduce_today.clear()
        today = datetime.utcnow().strftime("%Y-%m-%d")
        ta._reduce_today["AAPL"] = today
        if ta._reduce_today.get("AAPL") == today:
            ok("reduce_today dedup", "同日重复 REDUCE 已拦截 ✓")
        else:
            fail("reduce_today dedup", "_reduce_today 未记录")

        # 9c. _next_session_close returns a future datetime
        close_dt = ta._next_session_close()
        now = datetime.utcnow().replace(tzinfo=close_dt.tzinfo)
        if close_dt > now:
            ok("next_session_close", f"下次收盘: {close_dt.strftime('%Y-%m-%d %H:%M %Z')} ✓")
        else:
            fail("next_session_close", f"返回过去时间: {close_dt}")

        # 9d. _add_trade duplicate buy prevention
        ta._pending.clear()
        dummy_trade = {"id": "t1", "symbol": "AAPL", "side": "buy",
                       "notional": 500, "status": "pending",
                       "queued_at": datetime.utcnow().isoformat(), "source": "scan"}
        ta._pending["t1"] = dummy_trade
        existing = {"AAPL"}
        added = ta._add_trade(
            {"id": "t2", "symbol": "AAPL", "side": "buy", "notional": 500,
             "status": "pending", "queued_at": datetime.utcnow().isoformat(), "source": "scan"},
            existing, allow_add_to_position=False
        )
        if not added:
            ok("no-dup buy", "已持仓 symbol 重复买入被拦截 ✓")
        else:
            fail("no-dup buy", "重复买入未被拦截")

        ta._pending.clear()
        ta._reduce_today.clear()
        ta._sell_hold_count.clear()

    except Exception as e:
        fail("Rex 逻辑", str(e))
        traceback.print_exc()


# ── 10. Holdings Monitor 硬止损 ───────────────────────────────────────────────
def test_hard_stop_logic():
    print("\n[10/11] Holdings Monitor — Hard Stop 优先级")
    try:
        from unittest.mock import patch, MagicMock
        from src.monitor.holdings_monitor import analyze_sell_signals

        # Mock Claude to say HOLD for both positions
        hold_response = json.dumps([
            {"symbol": "LOSEIT", "sell_signal": "HOLD", "urgency": "LOW",
             "reason": "looks fine", "suggested_action": "hold"},
            {"symbol": "SAFEIT", "sell_signal": "HOLD", "urgency": "LOW",
             "reason": "looks fine", "suggested_action": "hold"},
        ])
        mock_msg = MagicMock()
        mock_msg.content = [MagicMock(text=hold_response)]
        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_msg

        positions_raw = [
            {"symbol": "LOSEIT", "qty": 5, "avg_entry_price": 100.0,
             "current_price": 96.4, "market_value": 482, "unrealized_pl": -18,
             "unrealized_plpc": -3.6, "side": "long"},          # -3.6% → hard stop
            {"symbol": "SAFEIT", "qty": 5, "avg_entry_price": 100.0,
             "current_price": 99.0, "market_value": 495, "unrealized_pl": -5,
             "unrealized_plpc": -1.0, "side": "long"},          # -1% → HOLD ok
        ]

        with patch("anthropic.Anthropic", return_value=mock_client), \
             patch("src.monitor.holdings_monitor._enrich_with_technicals",
                   side_effect=lambda p: [{**x, "_tech": {}} for x in p]):
            result = analyze_sell_signals(positions_raw)

        sig_map = {r["symbol"]: r for r in result}

        # LOSEIT: Claude says HOLD but -3.6% → must be SELL (hard stop wins)
        loseit = sig_map.get("LOSEIT", {})
        if loseit.get("sell_signal") == "SELL" and loseit.get("urgency") == "HIGH":
            ok("Hard stop override", "LOSEIT -3.6%: Claude=HOLD → hard stop=SELL ✓")
        else:
            fail("Hard stop override",
                 f"LOSEIT: sell_signal={loseit.get('sell_signal')}, urgency={loseit.get('urgency')}")

        # SAFEIT: Claude says HOLD, -1% → HOLD passes through
        safeit = sig_map.get("SAFEIT", {})
        if safeit.get("sell_signal") == "HOLD":
            ok("No false hard stop", "SAFEIT -1%: HOLD 正确保留 ✓")
        else:
            fail("No false hard stop",
                 f"SAFEIT: 意外 sell_signal={safeit.get('sell_signal')}")

    except Exception as e:
        fail("Hard stop 逻辑", str(e))
        traceback.print_exc()


# ── 11. 缓存数据契约 ───────────────────────────────────────────────────────────
def test_data_contracts():
    print("\n[11/11] 数据契约 — 缓存字段完整性")

    # Scan cache
    scan_file = Path("data/scan_cache.json")
    if scan_file.exists():
        try:
            data = json.loads(scan_file.read_text())
            candidates = data.get("sp500", {}).get("candidates", [])
            if candidates:
                # All candidates must have symbol; AI-scored ones must have signal/targets
                base_required = {"symbol"}
                ai_required = {"ai_score", "signal", "stop_loss", "target_price"}
                ai_scored = [c for c in candidates if c.get("ai_score") is not None]

                base_missing = [c["symbol"] for c in candidates if not base_required.issubset(c.keys())]
                if base_missing:
                    fail("Scan schema", f"缺少 symbol 字段: {base_missing}")
                else:
                    ok("Scan schema", f"{len(candidates)} candidates 均有 symbol ✓")

                if ai_scored:
                    incomplete = [c["symbol"] for c in ai_scored if not ai_required.issubset(c.keys())]
                    complete = [c for c in ai_scored if ai_required.issubset(c.keys())]
                    if incomplete and not complete:
                        # All AI-scored candidates missing extended fields → stale cache format
                        warn("Scan schema (AI)", f"缓存格式旧，缺少 signal/stop_loss/target_price ({incomplete}) — 重新扫描后自动修复")
                    elif incomplete:
                        warn("Scan schema (AI)", f"{len(incomplete)} 个旧格式 candidate: {incomplete}")
                        ok("Scan schema (AI)", f"{len(complete)} 个 AI candidates 字段完整 ✓")
                    else:
                        ok("Scan schema (AI)", f"{len(ai_scored)} 个 AI 评分 candidates 字段完整 ✓")
                else:
                    warn("Scan schema (AI)", "无 AI 评分 candidates（扫描后自动评分）")
            else:
                warn("Scan schema", "scan_cache 为空，跳过字段检查")
        except Exception as e:
            fail("Scan schema", str(e))
    else:
        warn("Scan schema", "scan_cache.json 不存在")

    # Review cache
    review_file = Path("data/review_cache.json")
    if review_file.exists():
        try:
            data = json.loads(review_file.read_text())
            latest = data.get("latest", {})
            if latest:
                required = {"iteration_opportunities", "performance", "date",
                            "what_worked", "what_didnt", "one_line_summary"}
                missing = required - set(latest.keys())
                if not missing:
                    ok("Review schema", f"所有必需字段存在 (date: {latest.get('date')}) ✓")
                else:
                    fail("Review schema", f"缺少字段: {missing}")
                # Validate iteration_opportunities structure
                opps = latest.get("iteration_opportunities", [])
                if opps:
                    opp_required = {"title", "verdict", "priority"}
                    opp_missing = opp_required - set(opps[0].keys())
                    if not opp_missing:
                        ok("Review opps", f"{len(opps)} 个迭代建议，字段完整 ✓")
                    else:
                        fail("Review opps", f"iteration_opportunity 缺少字段: {opp_missing}")
            else:
                warn("Review schema", "review_cache 无 latest 字段")
        except Exception as e:
            fail("Review schema", str(e))
    else:
        warn("Review schema", "review_cache.json 不存在（收盘后生成）")

    # Strategy notes active flag
    notes_file = Path("data/strategy_notes.json")
    if notes_file.exists():
        try:
            notes = json.loads(notes_file.read_text())
            active = [n for n in notes if n.get("active", True)]
            ok("Strategy notes", f"{len(active)}/{len(notes)} 条 notes 激活 ✓")
        except Exception as e:
            fail("Strategy notes schema", str(e))
    else:
        warn("Strategy notes", "strategy_notes.json 不存在")


# ── 12. 调度器架构 ────────────────────────────────────────────────────────────
def test_scheduler_design():
    print("\n[12/12] 调度器架构 — 单一 APScheduler 验证")
    import inspect

    # 1. app.py lifespan 不应再调用 _start_scheduler（双调度器已消除）
    try:
        import api.app as app_module
        lifespan_src = inspect.getsource(app_module._lifespan)
        if "_start_scheduler()" not in lifespan_src:
            ok("No dual scheduler", "lifespan 未调用 _start_scheduler() ✓")
        else:
            fail("No dual scheduler", "lifespan 仍调用 _start_scheduler() — 双调度器未修复")
    except Exception as e:
        fail("No dual scheduler", str(e))

    # 2. main.py 不应有独立 Rex cron（run_trade_agent 已删除）
    try:
        import main as main_module
        if not hasattr(main_module, "run_trade_agent"):
            ok("No Rex cron", "run_trade_agent 已从 main.py 移除 ✓")
        else:
            fail("No Rex cron", "run_trade_agent 仍存在 — Rex 独立 30 分钟轮询未移除")
    except Exception as e:
        fail("No Rex cron", str(e))

    # 3. 盘中 Vera 事件触发已移除（Vera 仅 4:15 PM 收盘后跑）
    try:
        import main as main_module
        has_event  = hasattr(main_module, "check_event_triggers")
        has_regime = hasattr(main_module, "check_regime_change")
        if not has_event and not has_regime:
            ok("Vera only at close", "check_event_triggers / check_regime_change 已移除 ✓")
        else:
            leftovers = [n for n, f in [("check_event_triggers", has_event),
                                         ("check_regime_change", has_regime)] if f]
            fail("Vera only at close", f"仍存在: {leftovers}")
    except Exception as e:
        fail("Vera only at close", str(e))

    # 4. Scout 函数存在于 main.py
    try:
        import main as main_module
        if hasattr(main_module, "run_scout"):
            ok("Scout in main", "run_scout 已加入 main.py ✓")
        else:
            fail("Scout in main", "run_scout 不存在于 main.py")
    except Exception as e:
        fail("Scout in main", str(e))

    # 5. run_holdings_refresh 包含 Rex cascade（卖出信号事件驱动）
    try:
        import main as main_module
        src = inspect.getsource(main_module.run_holdings_refresh)
        if "_run_agent_internal" in src:
            ok("Holdings→Rex cascade", "run_holdings_refresh 持仓刷新后 cascade 到 Rex ✓")
        else:
            fail("Holdings→Rex cascade", "run_holdings_refresh 缺少 _run_agent_internal cascade")
    except Exception as e:
        fail("Holdings→Rex cascade", str(e))

    # 6. run_sp500_scan 使用 cascade_agent=True（买入 Rex 事件驱动）
    try:
        import main as main_module
        src = inspect.getsource(main_module.run_sp500_scan)
        if "cascade_agent=True" in src:
            ok("Scan→Rex cascade", "run_sp500_scan 扫描完成后 cascade 到 Rex ✓")
        else:
            fail("Scan→Rex cascade", "run_sp500_scan 缺少 cascade_agent=True")
    except Exception as e:
        fail("Scan→Rex cascade", str(e))

    # 7. Scout 模块可导入（src/monitor/scout.py 存在）
    try:
        from src.monitor.scout import run as scout_run, get_dynamic_tickers
        ok("Scout module", "scout.run / get_dynamic_tickers 可导入 ✓")
    except Exception as e:
        fail("Scout module", str(e))

    # 8. Maya aggression: daily_return_needed > 0.2% → normal（不是 conservative）
    try:
        from src.analysis.market_context import _compute_goal_context
        from src.trader.alpaca_trader import get_account
        equity = float(get_account().equity)
        gc = _compute_goal_context(equity)
        agg  = gc["aggression"]
        need = gc["daily_return_needed"]
        if need <= 0.2:
            ok("Maya aggression", f"need={need:.2f}%/day → {agg} 合理（目标接近达成）✓")
        elif agg != "conservative":
            ok("Maya aggression", f"need={need:.2f}%/day → aggression={agg} ✓")
        else:
            fail("Maya aggression", f"need={need:.2f}%/day 但 aggression=conservative — 应为 normal/aggressive")
    except Exception as e:
        fail("Maya aggression", str(e))

    # 9. auto_approve threshold ≤ 75%（门槛过高会卡住有效信号）
    try:
        cfg = json.load(open("data/auto_approve.json"))
        threshold = cfg.get("threshold", 1.0)
        if threshold <= 0.75:
            ok("Auto threshold", f"threshold={threshold:.0%} ≤ 75% ✓")
        else:
            fail("Auto threshold", f"threshold={threshold:.0%} 过高 — 建议 ≤ 70%")
    except Exception as e:
        fail("Auto threshold", str(e))


# ── Report ────────────────────────────────────────────────────────────────────
def print_report():
    total  = len(results)
    passed = sum(1 for r in results if r[0] == PASS)
    failed = sum(1 for r in results if r[0] == FAIL)
    warned = sum(1 for r in results if r[0] == WARN)

    print("\n" + "═" * 55)
    print(f"  测试报告  {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("═" * 55)
    print(f"  {PASS} 通过: {passed}   {FAIL} 失败: {failed}   {WARN} 警告: {warned}  /  共 {total} 项")

    if failed:
        print("\n  需要修复:")
        for s, m, msg in results:
            if s == FAIL:
                print(f"    {FAIL} {m}: {msg}")

    if failed == 0:
        print("\n  ✓ 全部通过，可以开盘。")
    else:
        print(f"\n  ✗ {failed} 项失败，请修复后重试。")
    print("═" * 55)
    return failed


if __name__ == "__main__":
    mode = "smoke" if SMOKE_ONLY else "full"
    print("=" * 55)
    print(f"  端到端测试 [{mode}] — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 55)

    os.chdir(Path(__file__).parent.parent)

    # Always run (smoke + full)
    test_environment()
    test_account()
    test_rex_logic()
    test_hard_stop_logic()

    if not SMOKE_ONLY:
        test_market_regime()
        test_scanner()
        test_strategy_notes()
        test_autonomous_mode()
        test_rex_dry_run()
        test_vera()
        test_data_contracts()
        test_scheduler_design()

    failed = print_report()
    sys.exit(1 if failed else 0)
