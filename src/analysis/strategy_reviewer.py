"""
Daily strategy review — 3-agent orchestrated design.

复盘 Agent 作为编排者：
  1. 收集数据（交易日志 + 回测缓存 + 扫描结果）
  2. 并行提问：交易 Agent + 回测 Agent 各自分析今天的问题
  3. 合成：复盘 Agent 读取两份分析 → 生成带辩论依据的迭代建议
  4. 每个迭代建议自带 trading_view / backtest_view / synthesis / verdict
     用户看到的是结论，辩论细节可折叠
"""
from __future__ import annotations
import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime
from typing import Optional


def _safe_float(v, default=0.0) -> float:
    try:
        return float(v) if v is not None else default
    except (TypeError, ValueError):
        return default


def _parse_json(text: str) -> dict | list:
    """容错解析 AI 返回的 JSON:剥 markdown、抽取 {}/[] 块、去尾逗号、容忍字符串内换行。"""
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text).strip()

    # 候选:原文 + 抽取的第一个 {...} / [...] 块(含周围杂文时)
    candidates = [text]
    for pattern in (r"\{.*\}", r"\[.*\]"):
        m = re.search(pattern, text, re.DOTALL)
        if m:
            candidates.append(m.group())

    for cand in candidates:
        # 每个候选再试:原样 / 去掉 } 或 ] 前的尾逗号
        for attempt in (cand, re.sub(r",(\s*[}\]])", r"\1", cand)):
            try:
                return json.loads(attempt, strict=False)   # strict=False 容忍字符串内控制字符
            except json.JSONDecodeError:
                continue
    raise ValueError("无法解析 AI 返回的 JSON(已尝试去尾逗号/抽块/strict=False)")


# ── Agent 1: 交易 Agent Analyst ───────────────────────────────────────────────

def _ask_trading_agent(ctx: dict) -> dict:
    """
    交易 Agent 视角：今天信号执行哪里出了问题？
    Returns {"issues": [...], "raw": str}
    """
    from src.config import get_anthropic_client

    signals_found  = ctx.get("signals_found", "N/A")
    trades_queued  = ctx.get("trades_queued", "N/A")
    executed       = ctx.get("executed", 0)
    rejected       = ctx.get("rejected", 0)
    expired        = ctx.get("expired", 0)
    daily_ret      = ctx.get("daily_return_pct", 0)
    scan_candidates = ctx.get("scan_candidates", [])
    orders_txt     = ctx.get("orders_txt", "No orders today")
    monthly_gap    = ctx.get("target_gap", 0)
    min_ai_score   = ctx.get("min_ai_score", 7)

    candidates_txt = "\n".join(
        f"  {c['symbol']}: {c.get('signal','')} score={c.get('ai_score','?')}/10"
        for c in scan_candidates[:8]
    ) or "  (无扫描数据)"

    prompt = f"""你是一个交易执行专家，负责分析今天交易 Agent 的运行情况。

今日执行数据：
  信号数：{signals_found}  |  排队：{trades_queued}  |  成交：{executed}  |  拒绝：{rejected}  |  过期：{expired}
  今日收益：{daily_ret:+.2f}%  |  月度目标缺口：{monthly_gap:+.1f}%
  当前最低 AI 分门槛：{min_ai_score}/10

今日成交订单：
{orders_txt}

扫描候选股（按评分）：
{candidates_txt}

请从【信号质量、执行逻辑、参数设置】三个维度分析今天出了什么问题或做得好的地方。
聚焦最重要的 1-3 个发现，每条给出具体数据支撑。

返回 JSON（不含 markdown）：
{{
  "issues": [
    {{"finding": "具体问题或亮点（1句话）", "data": "支撑数据", "direction": "increase|decrease|maintain|fix"}}
  ]
}}"""

    msg = get_anthropic_client().messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=600,
        temperature=0,
        messages=[{"role": "user", "content": prompt}],
    )
    text = msg.content[0].text
    try:
        result = _parse_json(text)
        return {"issues": result.get("issues", []), "raw": text}
    except Exception:
        return {"issues": [], "raw": text}


# ── Agent 2: 回测 Agent Analyst ───────────────────────────────────────────────

def _ask_backtest_agent(ctx: dict) -> dict:
    """
    回测 Agent 视角：当前策略参数在历史数据上是否支持今天的行为？
    Returns {"issues": [...], "raw": str}
    """
    from src.config import get_anthropic_client

    bt = ctx.get("backtest", {})
    win_rate      = bt.get("win_rate", "N/A")
    profit_factor = bt.get("profit_factor", "N/A")
    total_return  = bt.get("total_return_pct", "N/A")
    spy_return    = bt.get("spy_return_pct", "N/A")
    alpha         = bt.get("alpha_pct", "N/A")
    max_dd        = bt.get("max_drawdown_pct", "N/A")
    sharpe        = bt.get("sharpe_ratio", "N/A")
    total_trades  = bt.get("total_trades", "N/A")
    exit_breakdown = bt.get("exit_breakdown", {})
    bt_params     = bt.get("params", {})

    exit_txt = "  " + " | ".join(f"{k}: {v}笔" for k, v in exit_breakdown.items()) if exit_breakdown else "  (无数据)"

    prompt = f"""你是一个量化回测专家，负责分析当前策略参数的历史表现。

最近一次回测结果：
  胜率：{win_rate}%  |  盈亏比：{profit_factor}  |  总收益：{total_return}%
  SPY 基准：{spy_return}%  |  超额收益：{alpha}%
  最大回撤：{max_dd}%  |  夏普比率：{sharpe}
  总交易次数：{total_trades}
  回测参数：{bt_params}

离场方式分布：
{exit_txt}

请从【参数合理性、统计显著性、风险控制】三个维度分析：
  1. 当前参数有什么统计上的问题或优势？
  2. 哪个指标最值得关注或调整？

聚焦最重要的 1-3 个发现，每条给出具体数据支撑。

返回 JSON（不含 markdown）：
{{
  "issues": [
    {{"finding": "具体发现（1句话）", "data": "数据依据", "direction": "increase|decrease|maintain|fix"}}
  ]
}}"""

    msg = get_anthropic_client().messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=600,
        temperature=0,
        messages=[{"role": "user", "content": prompt}],
    )
    text = msg.content[0].text
    try:
        result = _parse_json(text)
        return {"issues": result.get("issues", []), "raw": text}
    except Exception:
        return {"issues": [], "raw": text}


# ── Agent 3: 复盘 Agent Synthesizer ──────────────────────────────────────────

def _synthesize(
    trading_analysis: dict,
    backtest_analysis: dict,
    perf_ctx: dict,
) -> dict:
    """
    复盘 Agent 读取两份分析，生成完整复盘报告。
    迭代建议每条自带 trading_view + backtest_view + synthesis + verdict。
    """
    from src.config import get_anthropic_client

    daily_pl   = perf_ctx.get("daily_pl", 0)
    daily_ret  = perf_ctx.get("daily_return_pct", 0)
    monthly_ret = perf_ctx.get("monthly_return_pct", 0)
    target_pct = perf_ctx.get("target_monthly_pct", 10.0)
    target_gap = perf_ctx.get("target_gap", 0)
    equity     = perf_ctx.get("current_equity", 0)
    today_str  = perf_ctx.get("date", date.today().isoformat())

    trading_issues  = trading_analysis.get("issues", [])
    backtest_issues = backtest_analysis.get("issues", [])

    trading_txt  = "\n".join(f"  - {i['finding']} [{i.get('data','')}]" for i in trading_issues) or "  (无问题)"
    backtest_txt = "\n".join(f"  - {i['finding']} [{i.get('data','')}]" for i in backtest_issues) or "  (无数据)"

    market_ctx  = perf_ctx.get("market_context_hint", "")
    orders_txt  = perf_ctx.get("orders_txt", "No orders today")

    prompt = f"""你是一个资深交易策略分析师（复盘 Agent），今天是 {today_str}。
投资者目标：每月 {target_pct}% 收益。

=== 今日表现 ===
日收益：${daily_pl:+,.0f} ({daily_ret:+.2f}%)
月累计：{monthly_ret:+.2f}%（目标 {target_pct}%，差距 {target_gap:+.1f}%）
组合权益：${equity:,.0f}

=== 今日成交 ===
{orders_txt}

=== 交易 Agent 分析 ===
{trading_txt}

=== 回测 Agent 分析 ===
{backtest_txt}

基于以上两个 agent 的分析，生成完整的每日策略复盘报告。

迭代建议数量：动态生成 2-4 条（只生成真正重要的，不要凑数）。
每条必须同时引用交易 Agent 和回测 Agent 的观点，给出具体的综合结论。
verdict 要明确：ADOPT（立即采纳）/ HOLD（观察1-2天）/ REJECT（不适合当前状况）

返回 JSON（不含 markdown）：
{{
  "market_context": "2句话：今天市场发生了什么，关键主题",
  "core_strategy_assessment": "3-4句话：策略整体表现，信号质量，执行情况",
  "what_worked": ["具体有效的点1", "具体有效的点2"],
  "what_didnt": ["具体问题1", "具体问题2"],
  "monthly_progress_note": "1句话：月度目标进度",
  "iteration_opportunities": [
    {{
      "title": "简短标题（10字以内）",
      "trading_view": "交易 Agent 视角：1-2句，引用具体数字",
      "backtest_view": "回测 Agent 视角：1-2句，引用具体统计",
      "synthesis": "复盘 Agent 综合结论：1-2句，给出明确行动建议",
      "verdict": "ADOPT|HOLD|REJECT",
      "priority": "HIGH|MEDIUM|LOW",
      "expected_impact": "预期影响，如 +1-2%月收益"
    }}
  ],
  "tomorrow_focus": "2-3句话：明天具体关注什么，哪些股票或信号",
  "one_line_summary": "一句话总结今天"
}}"""

    msg = get_anthropic_client().messages.create(
        model="claude-sonnet-4-6",
        max_tokens=2048,
        messages=[{"role": "user", "content": prompt}],
    )
    text = msg.content[0].text
    try:
        return _parse_json(text)
    except Exception as e:
        # 解析失败也别让整份复盘崩(2026-06-29 实证 Phase3 JSON 报错整份失败)。
        # 降级:返回结构 + 保留原文供人工看。
        print(f"[review] Phase3 JSON 解析失败,降级保留原文: {e}")
        return {
            "one_line_summary": "复盘已生成,但 AI 综合输出 JSON 解析失败(已降级,见原文)",
            "market_context": "",
            "core_strategy_assessment": text[:2000],
            "what_worked": [],
            "what_didnt": [],
            "monthly_progress_note": "",
            "iteration_opportunities": [],
            "tomorrow_focus": "",
            "_parse_error": str(e),
        }


# ── Public entry point ────────────────────────────────────────────────────────

def generate_strategy_review(
    portfolio_history: dict,
    executed_orders: list[dict],
    agent_log: list[dict],
    agent_trades: list[dict],
    scan_result: dict,
    backtest_result: Optional[dict] = None,
    monthly_target_pct: float = 10.0,
) -> dict:
    """
    Orchestrate 3-agent strategy review.
    Phase 1: collect data
    Phase 2: parallel Trading + Backtest agent calls
    Phase 3: Synthesis agent → final report with pre-debated iterations
    """
    today_str = date.today().isoformat()

    # ── Phase 1: data collection ──────────────────────────────────────────────
    days = portfolio_history.get("days", [])
    today_day = next((d for d in reversed(days) if d["date"] == today_str), None)
    daily_pl    = _safe_float(today_day["daily_pl"] if today_day else 0)
    daily_ret   = _safe_float(today_day["daily_return_pct"] if today_day else 0)
    current_equity = _safe_float(portfolio_history.get("current_equity", 0))

    month_start = today_str[:7] + "-01"
    month_days  = [d for d in days if d["date"] >= month_start]
    if month_days and _safe_float(month_days[0]["equity"]) > 0:
        base = _safe_float(month_days[0]["equity"]) - _safe_float(month_days[0]["daily_pl"])
        monthly_ret = (current_equity - base) / base * 100 if base else 0
    else:
        monthly_ret = _safe_float(portfolio_history.get("total_return_pct", 0))

    target_gap = monthly_target_pct - monthly_ret

    today_log      = next((l for l in agent_log if l["run_at"][:10] == today_str), None)
    today_trades   = [t for t in agent_trades if t.get("created_at", "")[:10] == today_str]
    executed_today = [t for t in today_trades if t["status"] == "executed"]
    rejected_today = [t for t in today_trades if t["status"] == "rejected"]
    expired_today  = [t for t in today_trades if t["status"] == "expired"]

    scan_candidates = (scan_result.get("candidates") or [])[:8]
    orders_txt = "\n".join(
        f"  {o['side'].upper()} {o['symbol']} qty={o.get('filled_qty', o.get('qty','?'))} "
        f"@ ${_safe_float(o.get('filled_avg_price',0)):.2f} [{o['status']}]"
        for o in executed_orders[:10]
    ) or "  今日无成交"

    # ── Phase 2: parallel agent calls ─────────────────────────────────────────
    trading_ctx = {
        "signals_found":   today_log["signals_found"] if today_log else "N/A",
        "trades_queued":   today_log["trades_queued"] if today_log else "N/A",
        "executed":        len(executed_today),
        "rejected":        len(rejected_today),
        "expired":         len(expired_today),
        "daily_return_pct": daily_ret,
        "target_gap":      target_gap,
        "min_ai_score":    today_log.get("min_ai_score", 7) if today_log else 7,
        "scan_candidates": scan_candidates,
        "orders_txt":      orders_txt,
    }

    bt = backtest_result or {}
    backtest_ctx = {
        "backtest": {
            "win_rate":         bt.get("win_rate"),
            "profit_factor":    bt.get("profit_factor"),
            "total_return_pct": bt.get("total_return_pct"),
            "spy_return_pct":   bt.get("spy_return_pct"),
            "alpha_pct":        bt.get("alpha_pct"),
            "max_drawdown_pct": bt.get("max_drawdown_pct"),
            "sharpe_ratio":     bt.get("sharpe_ratio"),
            "total_trades":     bt.get("total_trades"),
            "exit_breakdown":   bt.get("exit_breakdown", {}),
            "params":           bt.get("params", {}),
        }
    }

    print("[review] Phase 2: running trading + backtest agents in parallel…")
    trading_analysis  = {"issues": [], "raw": ""}
    backtest_analysis = {"issues": [], "raw": ""}

    with ThreadPoolExecutor(max_workers=2) as pool:
        fut_trading  = pool.submit(_ask_trading_agent,  trading_ctx)
        fut_backtest = pool.submit(_ask_backtest_agent, backtest_ctx)
        for fut in as_completed([fut_trading, fut_backtest]):
            if fut is fut_trading:
                trading_analysis  = fut.result()
            else:
                backtest_analysis = fut.result()

    print(f"[review] Trading issues: {len(trading_analysis['issues'])}  Backtest issues: {len(backtest_analysis['issues'])}")

    # ── Phase 3: synthesis ────────────────────────────────────────────────────
    perf_ctx = {
        "date":               today_str,
        "daily_pl":           daily_pl,
        "daily_return_pct":   daily_ret,
        "monthly_return_pct": monthly_ret,
        "target_monthly_pct": monthly_target_pct,
        "target_gap":         target_gap,
        "current_equity":     current_equity,
        "orders_txt":         orders_txt,
    }

    print("[review] Phase 3: synthesizing final review…")
    review = _synthesize(trading_analysis, backtest_analysis, perf_ctx)

    review.update({
        "date":         today_str,
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "performance": {
            "daily_pl":           round(daily_pl, 2),
            "daily_return_pct":   round(daily_ret, 2),
            "monthly_return_pct": round(monthly_ret, 2),
            "target_monthly_pct": monthly_target_pct,
            "target_gap":         round(target_gap, 2),
            "current_equity":     round(current_equity, 2),
        },
        "_agent_debug": {
            "trading_issues":  trading_analysis.get("issues", []),
            "backtest_issues": backtest_analysis.get("issues", []),
        },
    })
    return review
