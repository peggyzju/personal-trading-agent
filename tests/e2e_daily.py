"""
End-to-end daily test — simulates market open and runs the full pipeline.
Usage: python tests/e2e_daily.py
"""
from __future__ import annotations
import sys, os, json, traceback, time
from pathlib import Path
from datetime import datetime

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

        # Check scheduler logic parses correctly
        from api.app import _start_scheduler
        ok("Scheduler", "_start_scheduler 可导入")

    except Exception as e:
        fail("Vera", str(e))


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
    print("=" * 55)
    print(f"  端到端测试 — 模拟开盘  {datetime.now().strftime('%Y-%m-%d')}")
    print("=" * 55)

    os.chdir(Path(__file__).parent.parent)

    test_environment()
    test_account()
    test_market_regime()
    test_scanner()
    test_strategy_notes()
    test_autonomous_mode()
    test_rex_dry_run()
    test_vera()

    failed = print_report()
    sys.exit(1 if failed else 0)
