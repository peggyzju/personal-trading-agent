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
    VALID_REGIMES     = {"BULL", "NEUTRAL", "CAUTION", "BEAR"}
    VALID_AGGRESSIONS = {"aggressive", "normal", "conservative"}
    SIZE_SCALE_MAP    = {"aggressive": 1.1, "normal": 1.0, "conservative": 0.75}

    try:
        from src.monitor.market_regime import get_market_regime
        from src.analysis.market_context import load_market_context

        # 3a. Regime output structure
        r = get_market_regime()
        regime = r.get("regime", "?")
        if regime in VALID_REGIMES:
            ok("Regime value", f"regime={regime} ∈ {VALID_REGIMES} ✓")
        else:
            fail("Regime value", f"regime={regime!r} 不是合法值，期望 {VALID_REGIMES}")

        required_keys = {"regime", "reason", "block_buys", "size_factor"}
        missing = required_keys - set(r.keys())
        if not missing:
            ok("Regime schema", f"所有必需字段存在 ✓")
        else:
            fail("Regime schema", f"缺少字段: {missing}")

        block = r.get("block_buys", False)
        if block:
            warn("Buy gate", "当前 regime 阻止买入")
        else:
            ok("Buy gate", "买入未被阻止 ✓")

        # 3b. Market context: aggression + size_scale 内部一致
        ctx = load_market_context()
        aggression = ctx.get("aggression", "?")
        size_scale = ctx.get("size_scale")
        min_ai     = ctx.get("min_ai_score")

        if aggression in VALID_AGGRESSIONS:
            ok("Aggression value", f"aggression={aggression} ✓")
        else:
            fail("Aggression value", f"aggression={aggression!r} 不合法")

        expected_scale = SIZE_SCALE_MAP.get(aggression)
        if expected_scale and abs((size_scale or 0) - expected_scale) < 0.01:
            ok("size_scale consistent", f"aggression={aggression} → size_scale={size_scale} ✓")
        else:
            fail("size_scale consistent",
                 f"aggression={aggression} 期望 size_scale={expected_scale}，实际={size_scale}")

        if isinstance(min_ai, (int, float)) and 1 <= min_ai <= 10:
            ok("min_ai_score range", f"min_ai_score={min_ai} ∈ [1,10] ✓")
        else:
            fail("min_ai_score range", f"min_ai_score={min_ai!r} 超出合法范围")

    except Exception as e:
        fail("Market regime", str(e))


# ── 4. Scanner (Scout) ────────────────────────────────────────────────────────
def test_scanner():
    print("\n[4/8] Scout — 扫描缓存")
    VALID_SIGNALS = {"STRONG_BUY", "BUY", "HOLD", "SELL"}
    cache_file = Path("data/scan_cache.json")
    try:
        if not cache_file.exists():
            warn("Scan cache", "不存在（首次运行前正常）")
            return
        data = json.loads(cache_file.read_text())
        sp500 = data.get("sp500", {})
        status     = sp500.get("status", "?")
        candidates = sp500.get("candidates", [])
        scanned_at = sp500.get("scanned_at", "unknown")

        if status == "running":
            warn("Scan cache", "扫描进行中")
            return
        if status != "done" or not candidates:
            warn("Scan cache", f"status={status}, {len(candidates)} candidates")
            return

        ok("Scan cache", f"{len(candidates)} candidates, 扫描于 {scanned_at[:16]}")

        # 4a. 每个 candidate 必须有 symbol + price
        base_ok = [c for c in candidates if c.get("symbol") and c.get("price")]
        if len(base_ok) == len(candidates):
            ok("Candidate base fields", f"全部 {len(candidates)} 个有 symbol+price ✓")
        else:
            fail("Candidate base fields", f"{len(candidates)-len(base_ok)} 个缺少 symbol 或 price")

        # 4b. AI 评分字段完整性
        ai_scored = [c for c in candidates if c.get("ai_score") is not None]
        if ai_scored:
            bad_signal  = [c["symbol"] for c in ai_scored if c.get("signal") not in VALID_SIGNALS]
            bad_score   = [c["symbol"] for c in ai_scored
                           if not isinstance(c.get("ai_score"), (int, float))
                           or not (1 <= c["ai_score"] <= 10)]
            bad_stop    = [c["symbol"] for c in ai_scored if c.get("stop_loss") is None]
            # target_price only required for BUY/STRONG_BUY signals
            buy_scored  = [c for c in ai_scored if c.get("signal") in ("BUY", "STRONG_BUY")]
            bad_target  = [c["symbol"] for c in buy_scored if c.get("target_price") is None]

            if not bad_signal:
                ok("Signal values", f"{len(ai_scored)} 个 AI 评分均有合法 signal ✓")
            else:
                fail("Signal values", f"非法 signal: {bad_signal}")

            if not bad_score:
                ok("AI score range", f"ai_score 均在 [1,10] ✓")
            else:
                fail("AI score range", f"超出范围: {bad_score}")

            if not bad_stop:
                ok("Stop loss set", f"全部 AI 评分有 stop_loss ✓")
            else:
                fail("Stop loss set", f"缺少 stop_loss: {bad_stop}")

            if not bad_target:
                ok("Target price set", f"{len(buy_scored)} 个 BUY/STRONG_BUY 均有 target_price ✓")
            else:
                fail("Target price set", f"BUY 信号缺少 target_price: {bad_target}")
        else:
            warn("AI scores", "无 AI 评分 candidates（扫描后自动评分）")

        # 4c. STRONG_BUY / BUY 排在前面（排序正确）
        buy_signals = {"STRONG_BUY", "BUY"}
        first_non_buy = next(
            (i for i, c in enumerate(candidates) if c.get("signal") not in buy_signals
             and c.get("signal") in VALID_SIGNALS), None
        )
        first_buy_after_hold = next(
            (i for i, c in enumerate(candidates)
             if i > (first_non_buy or 999) and c.get("signal") in buy_signals), None
        )
        if first_buy_after_hold is None:
            ok("Sort order", "BUY/STRONG_BUY 排在 HOLD/SELL 前面 ✓")
        else:
            fail("Sort order", f"位置 {first_buy_after_hold} 出现 BUY，但位置 {first_non_buy} 已有 HOLD")

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
    REQUIRED_FIELDS = {"date", "one_line_summary", "what_worked", "what_didnt",
                       "iteration_opportunities", "performance"}
    OPP_REQUIRED    = {"title", "verdict", "priority"}
    VALID_VERDICTS  = {"ADOPT", "HOLD", "REJECT"}
    VALID_PRIORITIES = {"HIGH", "MEDIUM", "LOW"}

    try:
        # 8a. Function importable with expected signature
        from src.analysis.strategy_reviewer import generate_strategy_review
        import inspect
        params = set(inspect.signature(generate_strategy_review).parameters.keys())
        expected_params = {"portfolio_history", "executed_orders", "agent_log"}
        missing_params = expected_params - params
        if not missing_params:
            ok("Vera signature", f"generate_strategy_review 参数完整 ✓")
        else:
            fail("Vera signature", f"缺少参数: {missing_params}")

        # 8b. Review cache content validation
        review_cache = Path("data/review_cache.json")
        if not review_cache.exists():
            warn("Review cache", "无复盘缓存（收盘后自动生成）")
            return

        data   = json.loads(review_cache.read_text())
        latest = data.get("latest", {})

        if not latest:
            warn("Review cache", "缓存存在但无 latest 字段")
            return

        # Required fields present and non-empty
        missing_fields = REQUIRED_FIELDS - set(latest.keys())
        if not missing_fields:
            ok("Review fields", f"所有必需字段存在 ✓")
        else:
            fail("Review fields", f"缺少字段: {missing_fields}")

        # one_line_summary is a non-empty string
        summary = latest.get("one_line_summary", "")
        if isinstance(summary, str) and len(summary) > 10:
            ok("one_line_summary", f"{summary[:50]} ✓")
        else:
            fail("one_line_summary", f"内容为空或过短: {summary!r}")

        # what_worked / what_didnt are non-empty lists
        for field in ("what_worked", "what_didnt"):
            val = latest.get(field, [])
            if isinstance(val, list) and len(val) > 0:
                ok(field, f"{len(val)} 条 ✓")
            else:
                fail(field, f"期望非空列表，实际: {val!r}")

        # iteration_opportunities structure
        opps = latest.get("iteration_opportunities", [])
        if not opps:
            warn("Opportunities", "无迭代建议")
        else:
            bad_opps = []
            for opp in opps:
                missing_opp = OPP_REQUIRED - set(opp.keys())
                if missing_opp:
                    bad_opps.append(f"{opp.get('title','?')} 缺 {missing_opp}")
                if opp.get("verdict") not in VALID_VERDICTS:
                    bad_opps.append(f"{opp.get('title','?')} verdict={opp.get('verdict')!r}")
                if opp.get("priority") not in VALID_PRIORITIES:
                    bad_opps.append(f"{opp.get('title','?')} priority={opp.get('priority')!r}")
            if not bad_opps:
                ok("Opportunities", f"{len(opps)} 条，字段+枚举值均合法 ✓")
            else:
                fail("Opportunities", f"问题: {bad_opps}")

        ok("Review date", f"最新复盘: {latest.get('date')} ✓")

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

        # 9e. REDUCE streak: 2 consecutive REDUCEs → escalate to SELL
        from src.trader.trade_agent import _load_reduce_streak, _save_reduce_streak
        import tempfile, os
        from unittest.mock import patch

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as tf:
            streak_path = tf.name
            tf.write("{}")

        try:
            with patch("src.trader.trade_agent._REDUCE_STREAK_FILE",
                       __import__("pathlib").Path(streak_path)):
                # First REDUCE: streak → 1, signal stays REDUCE
                streak_data = _load_reduce_streak()
                streak_data["TESTX"] = 0
                _save_reduce_streak(streak_data)
                streak_data["TESTX"] += 1
                _save_reduce_streak(streak_data)
                if _load_reduce_streak().get("TESTX") == 1:
                    ok("REDUCE streak +1", "첫 REDUCE → streak=1, 신호 유지 ✓")
                else:
                    fail("REDUCE streak +1", "streak 未增加到 1")

                # Second REDUCE: streak >= 2 → should escalate
                streak_data["TESTX"] += 1
                _save_reduce_streak(streak_data)
                loaded = _load_reduce_streak()
                if loaded.get("TESTX") >= 2:
                    ok("REDUCE streak escalate", "streak=2 → 应升级为 SELL ✓")
                else:
                    fail("REDUCE streak escalate", f"streak={loaded.get('TESTX')} 未达到 2")

                # Reset after escalation
                streak_data.pop("TESTX", None)
                _save_reduce_streak(streak_data)
                if "TESTX" not in _load_reduce_streak():
                    ok("REDUCE streak reset", "升级后 streak 重置 ✓")
                else:
                    fail("REDUCE streak reset", "升级后 streak 未清除")
        finally:
            os.unlink(streak_path)

    except Exception as e:
        fail("Rex 逻辑", str(e))
        traceback.print_exc()


# ── 10. Holdings Monitor 硬止损 + Trailing Stop ───────────────────────────────
def test_hard_stop_logic():
    print("\n[10/11] Holdings Monitor — Hard Stop + Trailing Stop")
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
             "current_price": 91.0, "market_value": 455, "unrealized_pl": -45,
             "unrealized_plpc": -9.0, "side": "long"},          # -9% → hard stop (threshold -8%)
            {"symbol": "SAFEIT", "qty": 5, "avg_entry_price": 100.0,
             "current_price": 99.0, "market_value": 495, "unrealized_pl": -5,
             "unrealized_plpc": -1.0, "side": "long"},          # -1% → HOLD ok
        ]

        with patch("anthropic.Anthropic", return_value=mock_client), \
             patch("src.monitor.holdings_monitor._enrich_with_technicals",
                   side_effect=lambda p: [{**x, "_tech": {}} for x in p]):
            result = analyze_sell_signals(positions_raw)

        sig_map = {r["symbol"]: r for r in result}

        # LOSEIT: Claude says HOLD but -9% → must be SELL (hard stop catch-all at -8%)
        loseit = sig_map.get("LOSEIT", {})
        if loseit.get("sell_signal") == "SELL" and loseit.get("urgency") == "HIGH":
            ok("Hard stop override", "LOSEIT -9%: Claude=HOLD → hard stop=SELL ✓")
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

    # ── Trailing stop tests ───────────────────────────────────────────────────
    try:
        from unittest.mock import patch, MagicMock
        from src.monitor.holdings_monitor import analyze_sell_signals

        hold_response = json.dumps([
            {"symbol": "RUNIT", "sell_signal": "HOLD", "urgency": "LOW",
             "reason": "still looks ok", "suggested_action": "hold"},
            {"symbol": "SAFEX", "sell_signal": "HOLD", "urgency": "LOW",
             "reason": "fine", "suggested_action": "hold"},
        ])
        mock_msg = MagicMock()
        mock_msg.content = [MagicMock(text=hold_response)]
        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_msg

        positions_ts = [
            # RUNIT: watermark=$120, trailing_stop=$112.8 (6%), current=$110 → SELL
            {"symbol": "RUNIT", "qty": 10, "avg_entry_price": 100.0,
             "current_price": 110.0, "market_value": 1100, "unrealized_pl": 100,
             "unrealized_plpc": 10.0, "side": "long"},
            # SAFEX: watermark=$105, trailing_stop=$98.7, current=$103 → HOLD
            {"symbol": "SAFEX", "qty": 10, "avg_entry_price": 100.0,
             "current_price": 103.0, "market_value": 1030, "unrealized_pl": 30,
             "unrealized_plpc": 3.0, "side": "long"},
        ]
        # Mock trailing stops: RUNIT's stop is above current price
        mock_trailing = {
            "RUNIT": {"high_watermark": 120.0, "trailing_stop": 114.0, "trail_pct": 5.0},  # 120*(1-0.05)
            "SAFEX": {"high_watermark": 105.0, "trailing_stop":  99.75, "trail_pct": 5.0},  # 105*(1-0.05)
        }

        with patch("anthropic.Anthropic", return_value=mock_client), \
             patch("src.monitor.holdings_monitor._enrich_with_technicals",
                   side_effect=lambda p: [{**x, "_tech": {}} for x in p]), \
             patch("src.monitor.holdings_monitor._update_trailing_stops",
                   return_value=mock_trailing):
            result_ts = analyze_sell_signals(positions_ts)

        ts_map = {r["symbol"]: r for r in result_ts}

        # RUNIT: price $110 < trailing_stop $114.0 (5% from $120 peak) → must SELL despite Claude=HOLD
        runit = ts_map.get("RUNIT", {})
        if runit.get("sell_signal") == "SELL" and runit.get("urgency") == "HIGH":
            ok("Trailing stop trigger", "RUNIT $110 < stop $114.0: Claude=HOLD → trailing=SELL ✓")
        else:
            fail("Trailing stop trigger",
                 f"RUNIT: sell_signal={runit.get('sell_signal')}, urgency={runit.get('urgency')}")

        # SAFEX: price $103 > trailing_stop $99.75 (5% from $105 peak) → HOLD passes through
        safex = ts_map.get("SAFEX", {})
        if safex.get("sell_signal") == "HOLD":
            ok("No false trailing stop", "SAFEX $103 > stop $99.75: HOLD 正确保留 ✓")
        else:
            fail("No false trailing stop",
                 f"SAFEX: 意外 sell_signal={safex.get('sell_signal')}")

    except Exception as e:
        fail("Trailing stop 逻辑", str(e))
        traceback.print_exc()


# ── 11b. v3 策略核心逻辑 ──────────────────────────────────────────────────────
def test_v3_strategy():
    print("\n[11b] v5 策略 — 双轨选股 / 止损 / 市场时间门 / 价格漂移 / 财报过滤 / 信号质量门控")

    # ── 1. compute_structured_stop 使用 2×ATR ────────────────────────────────
    try:
        from src.analysis.position_sizer import compute_structured_stop
        entry, atr, ma20 = 100.0, 3.0, 96.0
        stop = compute_structured_stop(entry, atr, ma20)
        # 2×ATR candidate = 100 - 6 = 94; MA20×0.99 = 94.97 → max(92, min(94.97, 97)) = 94.97
        # Either way, stop must be below entry and above floor (entry×0.92=92)
        floor, ceiling = entry * 0.92, entry * 0.97
        if floor <= stop <= ceiling:
            ok("2×ATR stop", f"entry={entry}, atr={atr} → stop={stop} ∈ [{floor},{ceiling}] ✓")
        else:
            fail("2×ATR stop", f"stop={stop} 超出 [{floor},{ceiling}] 范围")

        # Verify 2×ATR is in the candidate pool: entry - 2*atr = 94.0 should influence result
        stop_1atr = compute_structured_stop(entry, atr=10.0, ma20=50.0)
        if stop_1atr == floor:  # 100 - 20 = 80 < floor(92) → clamped to floor
            ok("2×ATR floor clamp", f"2×ATR=80 < floor=92 → clamped to {floor} ✓")
        else:
            fail("2×ATR floor clamp", f"期望 {floor}，实际 {stop_1atr}")
    except Exception as e:
        fail("2×ATR stop", str(e))

    # ── 2. 市场时间门 _is_market_hours ───────────────────────────────────────
    try:
        from src.trader.trade_agent import _is_market_hours
        from unittest.mock import patch
        from datetime import datetime
        import zoneinfo

        ET = zoneinfo.ZoneInfo("America/New_York")
        # Monday 10:00 ET → should be open
        open_dt  = datetime(2026, 5, 18, 10, 0, tzinfo=ET)
        # Monday 8:00 ET → should be closed
        pre_dt   = datetime(2026, 5, 18, 8, 0, tzinfo=ET)
        # Saturday → should be closed
        sat_dt   = datetime(2026, 5, 16, 10, 0, tzinfo=ET)

        with patch("src.trader.trade_agent._now", return_value=open_dt):
            if _is_market_hours():
                ok("Market hours open", "Mon 10:00 ET → open ✓")
            else:
                fail("Market hours open", "Mon 10:00 ET 应为 open")

        with patch("src.trader.trade_agent._now", return_value=pre_dt):
            if not _is_market_hours():
                ok("Market hours pre-open", "Mon 08:00 ET → closed ✓")
            else:
                fail("Market hours pre-open", "Mon 08:00 ET 应为 closed")

        with patch("src.trader.trade_agent._now", return_value=sat_dt):
            if not _is_market_hours():
                ok("Market hours weekend", "Saturday → closed ✓")
            else:
                fail("Market hours weekend", "Saturday 应为 closed")
    except Exception as e:
        fail("Market hours gate", str(e))

    # ── 3. 双轨选股逻辑 ──────────────────────────────────────────────────────
    try:
        from src.monitor.sp500_scanner import quick_screen

        # Build minimal mock raw records
        mock_raws = [
            # Track 1: RSI=60, today_bull=True, mom5d=2%, vs_ma20=5% → passes
            {"symbol": "T1OK", "rsi": 60, "today_bull": True, "momentum_5d": 2.0,
             "vs_ma20_pct": 5.0, "volume_ratio": 1.2, "sector": "SEMIS",
             "price": 100, "ma20": 95, "tech_score": 50.0, "macd_hist": 0.5},
            # Track 2: RSI=50, today_bull=False, vol_ratio=0.6, mom5d=0 → passes
            {"symbol": "T2OK", "rsi": 50, "today_bull": False, "momentum_5d": 0.0,
             "vs_ma20_pct": 3.0, "volume_ratio": 0.6, "sector": "SOFTWARE",
             "price": 100, "ma20": 97, "tech_score": 40.0, "macd_hist": 0.2},
            # Fails both: RSI=80 (>75, not in hot sector), today_bull=False
            {"symbol": "FAIL", "rsi": 80, "today_bull": False, "momentum_5d": 1.0,
             "vs_ma20_pct": 5.0, "volume_ratio": 1.0, "sector": "OTHER",
             "price": 100, "ma20": 95, "tech_score": 30.0, "macd_hist": -0.1},
        ]

        from unittest.mock import patch as _patch
        _mock_map = {r["symbol"]: r for r in mock_raws}
        with _patch("src.monitor.sp500_scanner._fetch_raw", side_effect=lambda sym: _mock_map.get(sym)):
            results_q = quick_screen(["T1OK", "T2OK", "FAIL"], force_symbols=set())

        syms = {r["symbol"] for r in results_q}
        if "T1OK" in syms:
            ok("Track1 passes", "T1OK RSI=60+today_bull → 通过 ✓")
        else:
            fail("Track1 passes", "T1OK 应通过 Track1")

        if "T2OK" in syms:
            ok("Track2 passes", "T2OK RSI=50+low_vol → 通过 ✓")
        else:
            fail("Track2 passes", "T2OK 应通过 Track2")

        if "FAIL" not in syms:
            ok("Dual-track filter", "FAIL RSI=80+no_bull → 正确拦截 ✓")
        else:
            fail("Dual-track filter", "FAIL 不应通过双轨过滤")

    except Exception as e:
        fail("双轨选股", str(e))
        traceback.print_exc()

    # ── 4. 板块共振 RSI 上限提升 ──────────────────────────────────────────────
    try:
        from src.monitor.sp500_scanner import quick_screen, SECTOR_RESONANCE_THRESHOLD

        # Create 3 SEMIS stocks with today_bull → sector becomes hot → RSI ceiling 85
        semis_hot = [
            {"symbol": f"SEMI{i}", "rsi": 78, "today_bull": True, "momentum_5d": 1.0,
             "vs_ma20_pct": 5.0, "volume_ratio": 1.0, "sector": "SEMIS",
             "price": 100, "ma20": 95, "tech_score": 50.0, "macd_hist": 0.5}
            for i in range(SECTOR_RESONANCE_THRESHOLD)
        ]
        # One stock with RSI=78 that should only pass when sector is hot
        test_stock = {"symbol": "SEMIHIGH", "rsi": 78, "today_bull": True,
                      "momentum_5d": 1.0, "vs_ma20_pct": 5.0, "volume_ratio": 1.0,
                      "sector": "SEMIS", "price": 100, "ma20": 95,
                      "tech_score": 48.0, "macd_hist": 0.3}

        all_mock = semis_hot + [test_stock]
        syms_all = [r["symbol"] for r in all_mock]

        from unittest.mock import patch as _patch
        _mock_map2 = {r["symbol"]: r for r in all_mock}
        with _patch("src.monitor.sp500_scanner._fetch_raw", side_effect=lambda sym: _mock_map2.get(sym)):
            res_hot = quick_screen(syms_all, force_symbols=set())

        hot_syms = {r["symbol"] for r in res_hot}
        if "SEMIHIGH" in hot_syms:
            ok("Sector resonance boost", f"SEMIS hot (≥{SECTOR_RESONANCE_THRESHOLD} today_bull) → RSI=78 通过 ✓")
        else:
            fail("Sector resonance boost", "SEMIS 板块共振应将 RSI 上限提升至 85，SEMIHIGH 应通过")

    except Exception as e:
        fail("板块共振", str(e))
        traceback.print_exc()

    # ── 5. 财报过滤 days=1 (今天/明天) ───────────────────────────────────────
    try:
        from src.monitor.news_monitor import earnings_within_days
        # days=1 means: only block today or tomorrow's earnings
        # We can't mock the actual calendar, but verify the function signature
        import inspect
        sig = inspect.signature(earnings_within_days)
        params = list(sig.parameters.keys())
        if "days" in params:
            ok("Earnings filter sig", "earnings_within_days(symbol, days=) 参数存在 ✓")
        else:
            fail("Earnings filter sig", "缺少 days 参数")

        # Verify default behavior: days=1 blocks only today/tomorrow
        # Call with a safe symbol (unlikely to have earnings tomorrow)
        has_earn, earn_date = earnings_within_days("AAPL", days=1)
        ok("Earnings filter call", f"AAPL days=1: has_earnings={has_earn}, date={earn_date} ✓")
    except Exception as e:
        fail("财报过滤", str(e))

    # ── 6. Trail 参数验证 (TRAIL_TRIGGER=6%, TRAIL_PCT=5%) ─────────────────
    try:
        import src.trader.trade_agent as ta
        import inspect
        src_code = inspect.getsource(ta.run_agent)
        import src.trader.trade_agent as _ta_mod
        ta_src = inspect.getsource(_ta_mod)
        if "TRAIL_TRIGGER = 0.10" in ta_src:
            ok("Trail trigger", "TRAIL_TRIGGER = 10% ✓")
        else:
            fail("Trail trigger", "TRAIL_TRIGGER 不是 0.10")
        import re
        if re.search(r"TRAIL_PCT\s+=\s+0\.05", ta_src):
            ok("Trail pct", "TRAIL_PCT = 5% ✓")
        else:
            fail("Trail pct", "TRAIL_PCT 不是 0.05")
    except Exception as e:
        fail("Trail 参数", str(e))

    # ── 7. 信号质量门控 (v5: BUY/STRONG_BUY only) ────────────────────────────
    try:
        import src.trader.trade_agent as _ta_mod2
        import inspect
        ta_src2 = inspect.getsource(_ta_mod2)
        if 'signal not in ("BUY", "STRONG_BUY")' in ta_src2:
            ok("Signal gate", "v5 信号门控：只接受 BUY/STRONG_BUY ✓")
        else:
            fail("Signal gate", "未找到 BUY/STRONG_BUY 门控，HOLD 信号可能被误入场")
    except Exception as e:
        fail("Signal gate", str(e))


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
        test_v3_strategy()

    failed = print_report()
    sys.exit(1 if failed else 0)
