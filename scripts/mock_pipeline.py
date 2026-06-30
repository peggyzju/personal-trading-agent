"""全链路自检 mock:Maya → Scout → Rex,只读 + 模拟决策,不下任何真实单。

用途:改动策略/买卖逻辑后,一键验证整条链路正确(市场环境→选股→交易决策→卖出),
并演示 v8 买入路由(无veto自动、veto进人工审核)。

运行:  PYTHONPATH=. python3 scripts/mock_pipeline.py
"""
from dotenv import load_dotenv; load_dotenv(".env")
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def stage_maya():
    print("=" * 70 + "\nSTAGE 1 — Maya(市场环境)\n" + "=" * 70)
    from src.monitor.market_regime import get_market_regime
    r = get_market_regime()
    print(f"  regime={r['regime']} | block_buys={r['block_buys']} | 仓位上限={r['max_positions']} | size_factor={r['size_factor']}")
    print(f"  vs_ma20={r['spy_vs_ma20']}% vs_ma50={r['spy_vs_ma50']}%")
    print(f"  reason: {r['reason']}")
    return r


def stage_scout(regime):
    print("\n" + "=" * 70 + "\nSTAGE 2 — Scout(扫描选股)\n" + "=" * 70)
    from src.monitor.sp500_scanner import quick_screen
    from src.analysis.stock_screener import ai_score_candidates
    tickers = sorted(set((ROOT / "data" / "sp500_constituents.txt").read_text().split()))
    top = quick_screen(tickers, top_n=25)
    print(f"  趋势门通过 → 返回 {len(top)} 候选(按动量排序)")
    scored = ai_score_candidates(top, market_context={"regime": regime["regime"]})
    scored = sorted(scored, key=lambda x: -(x.get("momentum_3m") or 0))
    print(f"  AI 排雷完成。前 10:")
    print(f"  {'#':<3}{'symbol':<7}{'动量3m':<9}{'veto':<7}排雷原因")
    for i, c in enumerate(scored[:10], 1):
        v = "🚫YES" if c.get("veto") else "✓no"
        print(f"  {i:<3}{c['symbol']:<7}{(c.get('momentum_3m') or 0):<9.0f}{v:<7}{str(c.get('veto_reason',''))[:45]}")
    return scored


def stage_rex(regime, scored):
    print("\n" + "=" * 70 + "\nSTAGE 3 — Rex(交易决策模拟,不下真实单)\n" + "=" * 70)
    from src.trader.alpaca_trader import get_client
    from src.monitor.news_monitor import earnings_within_days
    api = get_client()
    positions = api.list_positions()
    owned = {p.symbol for p in positions}
    acct = api.get_account()
    equity = float(acct.equity)
    cash = max(0.0, equity - sum(float(p.market_value) for p in positions))
    slots = max(0, regime["max_positions"] - len(positions))
    print(f"  现金 ${cash:,.0f} | 持仓 {len(positions)}/{regime['max_positions']} | 空位 {slots}")
    print(f"  持仓: {sorted(owned)}")

    print("\n  --- 买入决策(逐候选)---")
    if regime["block_buys"]:
        print("  ⛔ regime 封锁买入 → 本轮不买")
    else:
        HARD = 0.08
        filled = 0
        for c in scored:
            sym = c["symbol"]
            if sym in owned:
                continue
            has_e, ed = earnings_within_days(sym, days=1)
            if has_e:
                print(f"  {sym:6} SKIP — 财报 {ed} 太近"); continue
            price = c.get("price") or 0
            stop = c.get("stop_loss") or (price * 0.92 if price else None)
            if price and stop and stop < price and (price - stop) / price > HARD + 0.005:
                print(f"  {sym:6} SKIP — 止损 {(price-stop)/price:.1%} > 8% 上限"); continue
            if c.get("veto"):
                print(f"  {sym:6} 🚫 人工审核 — AI 排雷: {str(c.get('veto_reason',''))[:40]}"); continue
            if filled >= slots:
                print(f"  {sym:6} ⏸  无空位(已满 {regime['max_positions']})— 排队等位"); continue
            filled += 1
            print(f"  {sym:6} ✅ 自动买入(动量{(c.get('momentum_3m') or 0):.0f})")

    print("\n  --- 卖出决策(机械)---")
    from src.monitor.holdings_monitor import analyze_sell_signals, get_paper_positions
    for p in analyze_sell_signals(get_paper_positions()):
        mark = "🔴 SELL" if p.get("sell_signal") == "SELL" else "   HOLD"
        print(f"  {p['symbol']:6} {mark}  {str(p.get('reason',''))[:50]}")


def demo_routing():
    """注入 veto 演示买入路由(无需扫描,确定性验证新逻辑)。"""
    print("\n" + "=" * 70 + "\n附:买入路由演示(无veto自动 / veto→人工审核)\n" + "=" * 70)
    from src.trader import trade_agent as ta
    cands = [
        ("AAA", False, ""),
        ("BBB", True, "财报后估值透支,过热"),
        ("CCC", False, ""),
    ]
    auto, manual = [], []
    for sym, v, vr in cands:
        t = ta._make_trade(symbol=sym, side="buy", notional=2000.0, qty=None, signal="HOLD",
                           confidence=0.8, reason=(f"🚫 AI 排雷(需人工确认): {vr}" if v else "动量买入"),
                           source="scanner", stop_loss=92.0, price=100.0,
                           veto=v, veto_reason=vr if v else None)
        if t.get("veto"):
            manual.append(sym); print(f"  {sym}  veto=True  → 🚫 人工审核队列 — {vr}")
        else:
            auto.append(sym); print(f"  {sym}  veto=False → ✅ 自动买入")
    assert auto == ["AAA", "CCC"] and manual == ["BBB"], "路由错误!"
    print("  ✓ 路由正确")


if __name__ == "__main__":
    regime = stage_maya()
    scored = stage_scout(regime)
    stage_rex(regime, scored)
    demo_routing()
    print("\n" + "=" * 70 + "\n全链路 mock 完成 — 无真实下单\n" + "=" * 70)
