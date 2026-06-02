"""
Market Context Generator
Runs before scan and agent, produces a shared market_context.json
that downstream agents read to align their behavior.

Produces:
  - regime: BULL / NEUTRAL / CAUTION / BEAR
  - sector_bias: {sector: "positive"/"negative"/"neutral"}
  - goal_context: progress toward 20-day 10-15% target
  - aggression: "conservative" / "normal" / "aggressive"
  - macro_flags: any known events today
"""
from __future__ import annotations
import json
from datetime import date, datetime, timezone
from pathlib import Path

_CONTEXT_FILE = Path(__file__).parent.parent.parent / "data" / "market_context.json"
_GOAL_FILE    = Path(__file__).parent.parent.parent / "data" / "goal_config.json"

# Sector ETF proxies for bias detection
SECTOR_ETFS = {
    "tech":          "XLK",
    "semiconductors":"SOXX",
    "healthcare":    "XLV",
    "energy":        "XLE",
    "financials":    "XLF",
    "industrials":   "XLI",
    "consumer":      "XLY",
    "utilities":     "XLU",
}


def _load_goal() -> dict:
    try:
        return json.loads(_GOAL_FILE.read_text())
    except Exception:
        return {"start_date": str(date.today()), "start_equity": 100_000.0,
                "target_pct_low": 10.0, "target_pct_high": 15.0, "total_days": 20}


def _compute_goal_context(current_equity: float) -> dict:
    goal = _load_goal()
    start_equity  = goal["start_equity"]
    target_low    = start_equity * (1 + goal["target_pct_low"]  / 100)
    target_mid    = start_equity * (1 + (goal["target_pct_low"] + goal["target_pct_high"]) / 200)
    total_days    = goal["total_days"]

    start_date = date.fromisoformat(goal["start_date"])
    today      = date.today()
    days_elapsed = max(1, (today - start_date).days + 1)
    days_remaining = max(1, total_days - days_elapsed + 1)

    current_return_pct = (current_equity - start_equity) / start_equity * 100
    target_return_pct  = goal["target_pct_low"]

    # How much do we still need per remaining day?
    needed_total     = target_low - current_equity
    daily_return_needed = (needed_total / current_equity / days_remaining * 100) if days_remaining > 0 else 0

    # Are we on track? (linear interpolation of target)
    expected_progress = target_return_pct * days_elapsed / total_days
    on_track = current_return_pct >= expected_progress * 0.8   # allow 20% slack

    # Aggression level based on gap vs days left
    gap_pct = target_return_pct - current_return_pct
    if gap_pct <= 0:
        aggression = "conservative"   # already at/past target
    elif daily_return_needed > 1.5:
        aggression = "aggressive"     # need >1.5%/day — push harder
    elif daily_return_needed > 0.2:
        aggression = "normal"         # need >0.2%/day — stay deployed (was 0.5)
    else:
        aggression = "conservative"

    return {
        "start_equity":          round(start_equity, 2),
        "current_equity":        round(current_equity, 2),
        "target_equity_low":     round(target_low, 2),
        "target_equity_mid":     round(target_mid, 2),
        "current_return_pct":    round(current_return_pct, 3),
        "target_return_pct":     target_return_pct,
        "days_elapsed":          days_elapsed,
        "days_remaining":        days_remaining,
        "daily_return_needed":   round(daily_return_needed, 3),
        "on_track":              on_track,
        "gap_pct":               round(gap_pct, 3),
        "aggression":            aggression,
    }


def _get_sector_bias() -> dict[str, str]:
    """Quick sector bias: compare today's % change vs SPY."""
    try:
        import yfinance as yf
        spy = yf.Ticker("SPY").fast_info
        spy_chg = (spy.last_price - spy.previous_close) / spy.previous_close * 100 if spy.previous_close else 0

        bias = {}
        for sector, etf in SECTOR_ETFS.items():
            try:
                info = yf.Ticker(etf).fast_info
                chg  = (info.last_price - info.previous_close) / info.previous_close * 100
                rel  = chg - spy_chg
                if rel > 0.5:
                    bias[sector] = "positive"
                elif rel < -0.5:
                    bias[sector] = "negative"
                else:
                    bias[sector] = "neutral"
            except Exception:
                bias[sector] = "neutral"
        return bias
    except Exception:
        return {s: "neutral" for s in SECTOR_ETFS}


def generate_market_context(save: bool = True) -> dict:
    """Full market context — call this once before scan and agent.

    save=True（默认）：写盘 market_context.json —— 仅供 Maya 8:00 定时任务用，
                       generated_at 代表"Maya 真正运行"的时间。
    save=False：只计算返回、不写盘 —— 供 load_market_context 在陈旧时按需重算，
                避免扫描/测试触发的重算污染 Maya 的 generated_at / 运行显示。
    """
    from src.monitor.market_regime import get_market_regime

    # 1. Market regime (already cached with TTL)
    regime_data = get_market_regime()
    regime      = regime_data.get("regime", "NEUTRAL")

    # 2. Live equity for goal tracking
    current_equity = 100_000.0
    try:
        from src.trader.alpaca_trader import get_account
        current_equity = float(get_account().equity)
    except Exception:
        pass

    # 3. Goal progress + aggression level
    goal_ctx = _compute_goal_context(current_equity)

    # 4. Sector bias (best-effort, don't fail if yfinance slow)
    sector_bias = _get_sector_bias()

    # 5. Derive agent params from aggression + regime
    # aggression order: conservative(0) < normal(1) < aggressive(2)
    _AGG_ORDER = {"conservative": 0, "normal": 1, "aggressive": 2}

    def _cap_agg(current: str, cap: str) -> str:
        return current if _AGG_ORDER.get(current, 1) <= _AGG_ORDER.get(cap, 1) else cap

    aggression = goal_ctx["aggression"]
    if regime == "BEAR":
        aggression = "conservative"
    elif regime == "CAUTION":
        aggression = _cap_agg(aggression, "normal")
    elif regime == "NEUTRAL":
        # NEUTRAL: no clear trend — cap at normal, no aggressive buys
        aggression = _cap_agg(aggression, "normal")

    # min_ai_score: conservative=8 (higher bar in weak/no-trend envs)
    min_ai_score_map = {"aggressive": 6, "normal": 6, "conservative": 8}
    # size_scale: scales position size up/down
    size_scale_map   = {"aggressive": 1.1, "normal": 1.0, "conservative": 0.75}

    context = {
        "regime":        regime,
        "block_buys":    regime_data.get("block_buys", False),
        "size_factor":   regime_data.get("size_factor", 1.0),
        "sector_bias":   sector_bias,
        "goal_context":  goal_ctx,
        "aggression":    aggression,
        "min_ai_score":  min_ai_score_map[aggression],
        "size_scale":    size_scale_map[aggression],
        "generated_at":  datetime.now(timezone.utc).isoformat(),
    }

    # NEUTRAL: extra gate — require min_ai_score≥8 and shrink size to 75%
    # Prevents aggressive buys in non-trending environments (root cause of STEP/VIPS losses)
    if regime == "NEUTRAL":
        context["min_ai_score"] = max(context["min_ai_score"], 8)
        context["size_scale"]   = min(context["size_scale"], 0.75)

    # Persist for other agents to read（仅 Maya 定时任务 save=True 时写盘）
    if save:
        try:
            _CONTEXT_FILE.parent.mkdir(exist_ok=True)
            _CONTEXT_FILE.write_text(json.dumps(context, indent=2))
        except Exception as e:
            print(f"[market_context] save error: {e}")

    return context


def load_market_context() -> dict:
    """Load latest context from disk (used by scanner/agent)."""
    try:
        if _CONTEXT_FILE.exists():
            ctx = json.loads(_CONTEXT_FILE.read_text())
            # Stale if >4h old
            gen = datetime.fromisoformat(ctx.get("generated_at", "2000-01-01T00:00:00+00:00"))
            if (datetime.now(timezone.utc) - gen).total_seconds() < 4 * 3600:
                return ctx
    except Exception:
        pass
    # Fallback: 按需重算（不写盘，避免污染 Maya 的 generated_at）
    return generate_market_context(save=False)
