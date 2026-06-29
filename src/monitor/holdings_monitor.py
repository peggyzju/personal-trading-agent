from __future__ import annotations
import json
import os
import re
import time as _time
from datetime import datetime, timezone
from pathlib import Path

import anthropic
from src.config import get_anthropic_key, get_anthropic_client

# ── Per-position signal cache (avoids re-calling Claude when price barely moved) ─
_signal_cache: dict[str, dict] = {}   # symbol → {signal, urgency, reason, price, ts}
_SIGNAL_TTL   = 60 * 60               # 1 hour
_PRICE_MOVE_THRESHOLD = 1.5           # re-analyse if price moved >1.5%

# ── Trailing stop config ──────────────────────────────────────────────────────
_TRAILING_FILE = Path(__file__).parent.parent.parent / "data" / "trailing_stops.json"
TRAIL_PCT = 6.0          # trail 6% below high watermark (wider than 3% hard stop → lets winners run)
TREND_FILTER_PCT = 3.0   # v8: 浮盈 ≥ 3% 就压制 AI REDUCE/SELL(原 5%)— 让赢家跑,交给追踪止盈管理退出


def _load_trailing_stops() -> dict:
    try:
        return json.loads(_TRAILING_FILE.read_text())
    except Exception:
        return {}


def _save_trailing_stops(data: dict):
    try:
        _TRAILING_FILE.parent.mkdir(parents=True, exist_ok=True)
        _TRAILING_FILE.write_text(json.dumps(data, indent=2))
    except Exception as e:
        print(f"[holdings] trailing stop save error: {e}")


def _update_trailing_stops(positions: list[dict]) -> dict:
    """
    Update high watermarks for all open positions and compute trailing stops.
    Initialises new positions at current price.
    Removes entries for positions that are no longer open.
    Returns {symbol: {"trailing_stop": float, "high_watermark": float, ...}}
    """
    stops = _load_trailing_stops()
    open_syms = {p["symbol"] for p in positions}

    for p in positions:
        sym   = p["symbol"]
        price = float(p.get("current_price") or 0)
        if not price:
            continue
        if sym not in stops:
            stops[sym] = {
                "high_watermark":  price,
                "trail_pct":       TRAIL_PCT,
                "initialized_at":  datetime.now(timezone.utc).isoformat(),
            }
            print(f"[holdings] Trailing stop initialised for {sym} @ ${price:.2f}")
        elif price > stops[sym].get("high_watermark", 0):
            old_wm = stops[sym]["high_watermark"]
            stops[sym]["high_watermark"] = price
            print(f"[holdings] {sym} watermark updated ${old_wm:.2f} → ${price:.2f}")

        wm = stops[sym]["high_watermark"]
        pct = stops[sym].get("trail_pct", TRAIL_PCT)
        stops[sym]["trailing_stop"] = round(wm * (1 - pct / 100), 2)

    # Purge closed positions
    for sym in list(stops.keys()):
        if sym not in open_syms:
            print(f"[holdings] Removing trailing stop for closed position {sym}")
            del stops[sym]

    _save_trailing_stops(stops)
    return stops

# Demo paper portfolio when Alpaca keys are not configured
DEMO_POSITIONS = [
    {"symbol": "NVDA", "qty": 1,  "avg_entry_price": 220.0, "current_price": 0, "market_value": 0,
     "unrealized_pl": 0, "unrealized_plpc": 0, "side": "long"},
    {"symbol": "APP",  "qty": 1,  "avg_entry_price": 300.0, "current_price": 0, "market_value": 0,
     "unrealized_pl": 0, "unrealized_plpc": 0, "side": "long"},
    {"symbol": "MOD",  "qty": 1,  "avg_entry_price": 100.0, "current_price": 0, "market_value": 0,
     "unrealized_pl": 0, "unrealized_plpc": 0, "side": "long"},
    {"symbol": "VRT",  "qty": 1,  "avg_entry_price": 150.0, "current_price": 0, "market_value": 0,
     "unrealized_pl": 0, "unrealized_plpc": 0, "side": "long"},
    {"symbol": "GOOG", "qty": 1,  "avg_entry_price": 170.0, "current_price": 0, "market_value": 0,
     "unrealized_pl": 0, "unrealized_plpc": 0, "side": "long"},
    {"symbol": "AAPL", "qty": 1,  "avg_entry_price": 200.0, "current_price": 0, "market_value": 0,
     "unrealized_pl": 0, "unrealized_plpc": 0, "side": "long"},
]


def get_paper_positions() -> list[dict]:
    """Try Alpaca paper API; fall back to demo positions with live prices."""
    from src.monitor.price_monitor import get_quote

    try:
        from src.trader.alpaca_trader import get_client
        api = get_client()
        raw = api.list_positions()
        positions = [
            {
                "symbol": p.symbol,
                "qty": float(p.qty),
                "avg_entry_price": float(p.avg_entry_price),
                "current_price": float(p.current_price),
                "market_value": float(p.market_value),
                "unrealized_pl": float(p.unrealized_pl),
                "unrealized_plpc": float(p.unrealized_plpc) * 100,
                "side": p.side,
            }
            for p in raw
        ]
        if positions:
            return positions
    except Exception:
        pass

    # demo mode: fill in live prices
    positions = []
    for demo in DEMO_POSITIONS:
        p = dict(demo)
        try:
            q = get_quote(p["symbol"])
            p["current_price"] = q["price"]
            p["market_value"] = round(p["qty"] * q["price"], 2)
            p["unrealized_pl"] = round(p["market_value"] - p["qty"] * p["avg_entry_price"], 2)
            p["unrealized_plpc"] = round(
                (p["current_price"] - p["avg_entry_price"]) / p["avg_entry_price"] * 100, 2
            )
        except Exception:
            pass
        positions.append(p)
    return positions


def _enrich_with_technicals(positions: list[dict]) -> list[dict]:
    """
    Add RSI, 5-day momentum, and ATR to each position for better sell decisions.
    Silently skips symbols that fail to fetch data.
    """
    from src.monitor.price_monitor import get_ohlcv
    from src.analysis.technical_indicators import compute_all

    enriched = []
    for p in positions:
        tech = {}
        try:
            ohlcv = get_ohlcv(p["symbol"], period="30d")
            if ohlcv is not None and len(ohlcv) >= 10:
                indicators = compute_all(ohlcv)
                closes = ohlcv["Close"].dropna()
                mom5 = (
                    (float(closes.iloc[-1]) - float(closes.iloc[-6])) / float(closes.iloc[-6]) * 100
                    if len(closes) >= 6 else 0
                )
                tech = {
                    "rsi": indicators.get("rsi"),
                    "macd_bullish": indicators.get("macd_bullish_cross", False),
                    "macd_bearish": indicators.get("macd_bearish_cross", False),
                    "vs_ma20_pct": indicators.get("vs_ma20_pct"),
                    "mom5d_pct": round(mom5, 2),
                    "atr_pct": indicators.get("atr_pct"),
                    "stop_2atr": indicators.get("stop_2atr"),
                }
        except Exception:
            pass
        enriched.append({**p, "_tech": tech})
    return enriched


def analyze_sell_signals(positions: list[dict]) -> list[dict]:
    """
    For each position, produce a sell signal assessment using Claude.
    Enriches positions with live technical indicators before analysis.
    Skips re-analysis if cached signal is fresh (<1h) and price hasn't moved >1.5%.
    Returns positions with sell_signal, urgency, reason, suggested_action added.
    """
    if not positions:
        return []

    now = _time.time()

    # ── trail_active bypass: Alpaca owns the exit, AI must stay silent ────────
    # When trail_active=True the stock has gained ≥+10% and a server-side
    # trailing stop is active on Alpaca. Any local AI SELL/REDUCE would race
    # with Alpaca's order. Bypass Claude entirely; only _rule_based_override
    # (hard stop / trailing_stop.json hit) can still trigger a SELL.
    trail_bypass: list[dict] = []
    remaining: list[dict] = []
    for p in positions:
        if p.get("trail_active"):
            print(f"[holdings] {p['symbol']} trail_active=True — AI bypassed, Alpaca manages exit")
            trail_bypass.append({**p, "sell_signal": "HOLD", "urgency": "LOW",
                                  "reason": "[trail_active] Trailing stop active — Alpaca manages exit"})
        else:
            remaining.append(p)
    # Replace positions list with only non-trail positions for the rest of analysis
    positions = remaining

    # ── Split positions: use cache vs. needs fresh analysis ───────────────────
    to_analyze: list[dict] = []
    cached_results: list[dict] = []

    for p in positions:
        sym   = p["symbol"]
        price = float(p.get("current_price") or 0)
        cache = _signal_cache.get(sym)

        if cache:
            age       = now - cache.get("ts", 0)
            old_price = cache.get("price", 0)
            price_chg = abs(price - old_price) / old_price * 100 if old_price else 999
            if age < _SIGNAL_TTL and price_chg < _PRICE_MOVE_THRESHOLD:
                # Return cached signal merged with fresh position data
                cached_results.append({**p, **{k: v for k, v in cache.items()
                                               if k not in ("ts", "price")}})
                continue
        to_analyze.append(p)

    if cached_results:
        hit = len(cached_results)
        print(f"[holdings] Signal cache: {hit} hit / {len(to_analyze)} fresh needed")

    if not to_analyze:
        return cached_results  # All served from cache

    # v8: 纯机械卖出 —— 撤掉 AI 软清仓(回测验证的是纯机械退出:止损 + 追踪止盈 + MA20破位)。
    # 不再调 Claude 评估卖出;sell_signal 全由 _rule_based_override 的机械规则决定。
    enriched = _enrich_with_technicals(to_analyze)

    def _tech_line(tech: dict) -> str:
        parts = []
        if tech.get("rsi") is not None:
            zone = "oversold" if tech["rsi"] < 35 else "overbought" if tech["rsi"] > 70 else "neutral"
            parts.append(f"RSI={tech['rsi']:.0f}({zone})")
        if tech.get("mom5d_pct") is not None:
            parts.append(f"5d_mom={tech['mom5d_pct']:+.1f}%")
        if tech.get("vs_ma20_pct") is not None:
            parts.append(f"vs_MA20={tech['vs_ma20_pct']:+.1f}%")
        if tech.get("macd_bullish"):
            parts.append("MACD_bullish_cross")
        if tech.get("macd_bearish"):
            parts.append("MACD_bearish_cross")
        if tech.get("atr_pct") is not None:
            parts.append(f"ATR={tech['atr_pct']:.1f}%/day")
        if tech.get("stop_2atr") is not None:
            parts.append(f"2xATR_stop=${tech['stop_2atr']:.2f}")
        return " | ".join(parts) if parts else "no tech data"

    def _trend_info(p: dict) -> str:
        """Compute gain-from-entry and trend-protected status for prompt context."""
        try:
            entry = float(p.get("avg_entry_price") or 0)
            cur   = float(p.get("current_price") or 0)
            if entry and cur:
                gain = (cur - entry) / entry * 100
                protected = gain >= TREND_FILTER_PCT
                return f"gain_from_entry={gain:+.1f}% trend_protected={'Yes' if protected else 'No'}"
        except Exception:
            pass
        return ""

    rows = "\n".join(
        f'{i+1}. {p["symbol"]} | entry=${p["avg_entry_price"]:.2f} | '
        f'now=${p["current_price"]:.2f} | P&L={p["unrealized_plpc"]:+.1f}% | '
        f'qty={p["qty"]} | {_trend_info(p)} | {_tech_line(p.get("_tech", {}))}'
        for i, p in enumerate(enriched)
    )

    prompt = f"""You are a portfolio risk manager. Assess each position for a sell/hold decision.
Use BOTH the P&L data AND the technical indicators (RSI, momentum, MACD, MA position).

CRITICAL TREND FILTER (highest priority rule):
- If trend_protected=Yes (position up ≥5% from entry) → return HOLD, do NOT return REDUCE.
  The mechanical trailing stop is already protecting this position.
  Only override to SELL if: price breaks below MA20 AND MACD bearish cross confirmed simultaneously.

Key guidelines:
- RSI < 35 + positive momentum → consider HOLD even if P&L is negative (possible recovery)
- RSI > 70 alone is NOT a sell signal if trend_protected=Yes — strong momentum stocks stay overbought
- Position below 2×ATR stop level → SELL (stop hit)
- MACD bullish cross + above MA20 → lean HOLD or ADD if fundamentals support
- Pure P&L alone is NOT sufficient reason to sell; combine with technicals

Positions:
{rows}

For each position return a JSON object:
{{
  "symbol": "...",
  "sell_signal": "SELL" | "REDUCE" | "HOLD" | "ADD",
  "urgency": "HIGH" | "MEDIUM" | "LOW",
  "reason": "one sentence citing both P&L and the key technical signal",
  "suggested_action": "e.g. sell all, sell half, hold with stop at $X, add X shares"
}}

Return ONLY a JSON array."""

    trailing_stops = _update_trailing_stops(enriched)

    HARD_STOP_PCT = -8.0   # last-resort catch-all; per-position structured stops are 4-8%

    def _rule_based_override(p: dict) -> dict | None:
        """
        Returns a forced sell dict if any rule-based condition is met, else None.
        Priority: hard stop > trailing stop > (Claude signal used as-is)
        """
        sym  = p["symbol"]
        plpc = p.get("unrealized_plpc", 0)
        price = float(p.get("current_price") or 0)

        # 1. Hard stop (absolute loss)
        if plpc <= HARD_STOP_PCT:
            return {"sell_signal": "SELL", "urgency": "HIGH",
                    "reason": f"Hard stop: position down {plpc:.1f}% (threshold {HARD_STOP_PCT}%)"}

        # 2. Trailing stop (protect profits)
        ts_data = trailing_stops.get(sym, {})
        ts      = ts_data.get("trailing_stop")
        wm      = ts_data.get("high_watermark")
        if ts and price and price <= ts:
            drawdown = (price - wm) / wm * 100 if wm else 0
            return {"sell_signal": "SELL", "urgency": "HIGH",
                    "reason": f"Trailing stop hit: ${price:.2f} ≤ ${ts:.2f} "
                              f"({drawdown:.1f}% from high of ${wm:.2f})"}

        # 3. v8 趋势破位:收盘价跌破 MA20 → 趋势结束,退出(= 回测的 price<MA20 退出)
        vs_ma20 = (p.get("_tech", {}) or {}).get("vs_ma20_pct")
        if vs_ma20 is not None and vs_ma20 < 0:
            return {"sell_signal": "SELL", "urgency": "MEDIUM",
                    "reason": f"趋势破位:跌破 MA20(vs_MA20 {vs_ma20:+.1f}%)"}

        return None

    sig_map: dict = {}   # v8: 无 AI 卖出信号,sell_signal 全由机械 override(止损/追踪/MA20破位)决定

    result = []
    for p in enriched:
        override = _rule_based_override(p)
        ai_sig   = sig_map.get(p["symbol"], {})
        if override:
            # Rule-based overrides always win (hard stop / trailing stop)
            merged = {**ai_sig, **override}
        else:
            # ── 趋势过滤器 post-processing: belt-and-suspenders ─────────────────
            # Even if Claude still returns REDUCE, override to HOLD when gain ≥ 5%
            if ai_sig.get("sell_signal") == "REDUCE":
                try:
                    entry = float(p.get("avg_entry_price") or 0)
                    cur   = float(p.get("current_price") or 0)
                    gain  = (cur - entry) / entry * 100 if entry else 0
                    if gain >= TREND_FILTER_PCT:
                        print(f"[holdings] {p['symbol']} 趋势过滤器: +{gain:.1f}% ≥ {TREND_FILTER_PCT}% — REDUCE→HOLD")
                        ai_sig = {**ai_sig,
                                  "sell_signal": "HOLD",
                                  "urgency": "LOW",
                                  "reason": f"[趋势过滤器] 涨幅{gain:.1f}%≥{TREND_FILTER_PCT}%，追踪止盈托底，屏蔽REDUCE"}
                except Exception:
                    pass
            merged = ai_sig if ai_sig else {"sell_signal": "HOLD", "urgency": "LOW", "reason": ""}

        final = {k: v for k, v in {**p, **merged}.items() if k != "_tech"}

        # Populate cache (only for non-overridden / stable signals)
        sym   = p["symbol"]
        price = float(p.get("current_price") or 0)
        _signal_cache[sym] = {
            "sell_signal":      final.get("sell_signal", "HOLD"),
            "urgency":          final.get("urgency", "LOW"),
            "reason":           final.get("reason", ""),
            "suggested_action": final.get("suggested_action", ""),
            "price":            price,
            "ts":               now,
        }

        result.append(final)

    # Merge cached + freshly analysed, preserving original order
    sym_to_fresh = {r["symbol"]: r for r in result}
    ordered = []
    for p in positions:
        sym = p["symbol"]
        if sym in sym_to_fresh:
            ordered.append(sym_to_fresh[sym])
        else:
            # Already in cached_results
            cached_hit = next((c for c in cached_results if c["symbol"] == sym), None)
            if cached_hit:
                ordered.append(cached_hit)

    # Re-append trail_active bypass positions (trail_stop _rule_based_override still applies)
    # Run them through _rule_based_override so hard stop / trailing_stop.json can still fire
    _update_trailing_stops(trail_bypass)   # keep high watermarks current
    for p in trail_bypass:
        override = _rule_based_override(p)
        if override:
            ordered.append({**p, **override})
        else:
            ordered.append(p)

    return ordered
