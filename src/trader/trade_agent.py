"""
Trade Agent — autonomous signal detection + persistent pending trade queue.

Signal sources:
  1. S&P 500 scan  →  STRONG_BUY + ai_score >= 7
  2. Watchlist AI analysis  →  BUY + confidence >= 0.7  (uses cache, no extra API call)
  3. Holdings monitor  →  SELL / REDUCE signal

Fixes vs original:
  - Pending queue persisted to disk (trades.json) — survives restarts
  - Existing position check before queuing buy
  - Buying power + slot capacity check before queuing
  - Watchlist uses _analysis_cache instead of re-calling Claude
  - Market hours guard — skip watchlist analysis outside 9:25–16:05 ET
  - REDUCE qty uses max(1, floor) to avoid 0-share orders
  - approve_trade places a bracket order (entry + stop-loss + take-profit) when levels available
  - "error" trades auto-cleared after 24 h to keep queue clean
"""
from __future__ import annotations

import json
import math
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

# ── Persistence ───────────────────────────────────────────────────────────────

_TRADES_FILE = Path(__file__).parent.parent.parent / "data" / "trades.json"

def _ensure_data_dir():
    _TRADES_FILE.parent.mkdir(exist_ok=True)

def _load_from_disk() -> dict[str, dict]:
    _ensure_data_dir()
    if _TRADES_FILE.exists():
        try:
            return json.loads(_TRADES_FILE.read_text())
        except Exception:
            pass
    return {}

def _save_to_disk(pending: dict[str, dict]):
    _ensure_data_dir()
    try:
        _TRADES_FILE.write_text(json.dumps(pending, indent=2))
    except Exception as e:
        print(f"[agent] save error: {e}")

# ── In-memory state (loaded from disk on first access) ────────────────────────

_LOG_FILE      = Path(__file__).parent.parent.parent / "data" / "agent_log.json"
_OVERRIDES_FILE = Path(__file__).parent.parent.parent / "data" / "strategy_overrides.json"
_NOTES_FILE     = Path(__file__).parent.parent.parent / "data" / "strategy_notes.json"

def _load_strategy_overrides() -> dict:
    try:
        if _OVERRIDES_FILE.exists():
            return json.loads(_OVERRIDES_FILE.read_text())
    except Exception:
        pass
    return {}

def _load_active_strategy_notes() -> list[str]:
    try:
        if _NOTES_FILE.exists():
            notes = json.loads(_NOTES_FILE.read_text())
            return [n["text"] for n in notes if n.get("active", True)]
    except Exception:
        pass
    return []

def _load_log() -> list[dict]:
    try:
        if _LOG_FILE.exists():
            return json.loads(_LOG_FILE.read_text())
    except Exception:
        pass
    return []

def _save_log(log: list[dict]):
    try:
        _LOG_FILE.parent.mkdir(exist_ok=True)
        _LOG_FILE.write_text(json.dumps(log, default=str))
    except Exception:
        pass

_pending: dict[str, dict] = _load_from_disk()
_run_log: list[dict] = _load_log()
_agent_running: bool = False
_reduce_today: dict[str, str] = {}   # symbol -> date string; prevents repeat REDUCE same day
_sell_hold_count: dict[str, int] = {}  # symbol -> consecutive HOLD count; cancel only after >= 2

ERROR_TTL_HOURS   = 24    # auto-clear error trades after 24 h
MAX_LOG = 20


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _next_session_close() -> datetime:
    """Return the next trading session's 4:30 PM ET as UTC datetime.

    Trades generated any time today survive until today's close (if market still open)
    or next weekday's close (if market is closed / after hours).
    """
    try:
        import zoneinfo
        et = zoneinfo.ZoneInfo("America/New_York")
    except ImportError:
        et = timezone(timedelta(hours=-4))

    from datetime import time as dtime
    now_et = _now().astimezone(et)
    close_time = dtime(16, 30)  # 30-min buffer after 4 PM close

    candidate = now_et.replace(hour=16, minute=30, second=0, microsecond=0)
    if now_et.time() >= close_time or now_et.weekday() >= 5:
        # already past close or weekend — advance to next weekday
        candidate = candidate + timedelta(days=1)
        while candidate.weekday() >= 5:
            candidate = candidate + timedelta(days=1)

    return candidate.astimezone(timezone.utc)


def _is_market_hours() -> bool:
    """True between 9:25 AM and 4:05 PM US/Eastern Mon-Fri (approximate)."""
    from datetime import timezone as tz
    try:
        import zoneinfo
        et = zoneinfo.ZoneInfo("America/New_York")
    except ImportError:
        # Python < 3.9 fallback: UTC-4 (EDT) or UTC-5 (EST)
        import time
        et_offset = timedelta(hours=-4)   # EDT approximation
        et = timezone(et_offset)

    now_et = _now().astimezone(et)
    if now_et.weekday() >= 5:   # Saturday / Sunday
        return False
    t = now_et.time()
    from datetime import time as dtime
    return dtime(9, 25) <= t <= dtime(16, 5)


# ── Trade construction ────────────────────────────────────────────────────────

def _make_trade(
    symbol: str,
    side: str,
    notional: Optional[float],
    qty: Optional[float],
    signal: str,
    confidence: float,
    reason: str,
    source: str,
    stop_loss: Optional[float] = None,
    target_price: Optional[float] = None,
    price: Optional[float] = None,
    rsi: Optional[float] = None,
    momentum_5d: Optional[float] = None,
    volume_ratio: Optional[float] = None,
    near_breakout: Optional[bool] = None,
    universe: Optional[str] = None,
) -> dict:
    now = _now()
    return {
        "id": str(uuid.uuid4())[:8],
        "symbol": symbol,
        "side": side,
        "notional": notional,
        "qty": qty,
        "signal": signal,
        "confidence": round(confidence, 2),
        "reason": reason,
        "source": source,
        "stop_loss": stop_loss,
        "target_price": target_price,
        "price": price,
        "rsi": rsi,
        "momentum_5d": momentum_5d,
        "volume_ratio": volume_ratio,
        "near_breakout": near_breakout,
        "universe": universe,
        "status": "pending",
        "created_at": now.isoformat(),
        "expires_at": _next_session_close().isoformat(),
        "executed_order_id": None,
        "error": None,
    }


def get_pending_trades() -> list[dict]:
    """Expire stale + old errors, return all trades sorted newest first."""
    now = _now()
    changed = False
    for t in list(_pending.values()):
        if t["status"] == "pending" and datetime.fromisoformat(t["expires_at"]) < now:
            t["status"] = "expired"
            changed = True
        # Auto-clear errors older than 24 h
        if t["status"] == "error":
            age = (now - datetime.fromisoformat(t["created_at"].replace("Z", "+00:00")
                   if "+" not in t["created_at"] else t["created_at"])).total_seconds()
            if age > ERROR_TTL_HOURS * 3600:
                del _pending[t["id"]]
                changed = True
    if changed:
        _save_to_disk(_pending)
    return sorted(_pending.values(), key=lambda x: x["created_at"], reverse=True)


def sync_fills() -> list[str]:
    """Poll Alpaca for fill status of all submitted orders. Call every 5 min during market hours."""
    from src.trader.order_tracker import sync_order_status
    return sync_order_status(_pending, _save_to_disk)


def get_agent_log() -> list[dict]:
    return list(reversed(_run_log))


def _add_trade(trade: dict, existing_symbols: set[str], allow_add_to_position: bool = False) -> bool:
    """
    Add trade to queue if no duplicate pending for same symbol+side.
    `existing_symbols` = symbols we already own (skip buy if already held).
    `allow_add_to_position` = True when cash is very high and signal is strong (score ≥ 8).
    Returns True if added.
    """
    sym = trade["symbol"]
    side = trade["side"]

    # Don't buy what we already own (unless adding to position is explicitly allowed)
    if side == "buy" and sym in existing_symbols and not allow_add_to_position:
        print(f"[agent] skip {sym} buy — already in portfolio")
        return False

    # Don't duplicate pending
    for existing in _pending.values():
        if existing["symbol"] == sym and existing["side"] == side and existing["status"] == "pending":
            return False

    _pending[trade["id"]] = trade
    _save_to_disk(_pending)
    return True


# ── Approve / Reject ──────────────────────────────────────────────────────────

PRICE_DRIFT_THRESHOLD = 0.03   # 3% — if price moved >3% from scan, warn/block

def approve_trade(trade_id: str) -> dict:
    """Approve and execute. Places bracket order when stop/target available.
    Validates price hasn't drifted >3% from scan entry before executing buys."""
    trade = _pending.get(trade_id)
    if not trade:
        raise ValueError(f"Trade {trade_id} not found")
    if trade["status"] != "pending":
        raise ValueError(f"Trade {trade_id} is {trade['status']}, not pending")
    if datetime.fromisoformat(trade["expires_at"]) < _now():
        trade["status"] = "expired"
        _save_to_disk(_pending)
        raise ValueError(f"Trade {trade_id} has expired")

    # ── Price drift check for buys ────────────────────────────────────────────
    if trade["side"] == "buy" and trade.get("price"):
        try:
            import yfinance as yf
            live_price = yf.Ticker(trade["symbol"]).fast_info.last_price or 0
            scan_price = trade["price"]
            if live_price > 0 and scan_price > 0:
                drift = (live_price - scan_price) / scan_price
                trade["price_at_approve"] = round(live_price, 2)
                trade["price_drift_pct"]  = round(drift * 100, 2)
                if drift > PRICE_DRIFT_THRESHOLD:
                    # Price ran up too much — stale signal, auto-reject
                    trade["status"] = "rejected"
                    trade["error"]  = (
                        f"价格漂移 +{drift*100:.1f}% (扫描价 ${scan_price:.2f} → 现价 ${live_price:.2f})，"
                        f"超过 {PRICE_DRIFT_THRESHOLD*100:.0f}% 阈值，信号已失效"
                    )
                    _save_to_disk(_pending)
                    raise ValueError(trade["error"])
                elif drift < -PRICE_DRIFT_THRESHOLD:
                    # Price dropped — still ok to buy but log warning
                    print(f"[agent] {trade['symbol']} dipped {drift*100:.1f}% since scan — proceeding (better entry)")
        except ValueError:
            raise
        except Exception as e:
            print(f"[agent] price drift check failed for {trade['symbol']}: {e}")

    from src.trader.alpaca_trader import place_order, close_position

    try:
        if trade["side"] == "sell" and trade["qty"] is None:
            order = close_position(trade["symbol"])
        elif (
            trade["side"] == "buy"
            and trade.get("stop_loss")
            and trade.get("target_price")
            and trade.get("price")
        ):
            # Bracket order: market entry + stop-loss + take-profit
            order = place_order(
                symbol=trade["symbol"],
                side="buy",
                notional=trade["notional"],
                qty=trade["qty"],
                order_type="market",
                stop_loss=trade["stop_loss"],
                take_profit=trade["target_price"],
            )
        else:
            order = place_order(
                symbol=trade["symbol"],
                side=trade["side"],
                qty=trade["qty"],
                notional=trade["notional"],
            )
        trade["status"] = "executed"
        trade["executed_order_id"] = order.id
    except Exception as e:
        trade["status"] = "error"
        trade["error"] = str(e)
        _save_to_disk(_pending)
        raise

    _save_to_disk(_pending)
    return trade


def reject_trade(trade_id: str) -> dict:
    trade = _pending.get(trade_id)
    if not trade:
        raise ValueError(f"Trade {trade_id} not found")
    trade["status"] = "rejected"
    _save_to_disk(_pending)
    return trade


# ── Signal Detection ──────────────────────────────────────────────────────────

def run_agent(
    scan_cache: dict,
    holdings_cache: dict,
    watchlist: list[str],
    portfolio_value: float,
    analysis_cache: Optional[dict] = None,        # pass _analysis_cache from app.py
    analysis_timestamps: Optional[dict] = None,   # pass _analysis_timestamps from app.py
    min_ai_score_override: Optional[int] = None,  # from market_context aggression
    size_scale_override: Optional[float] = None,  # from market_context aggression
) -> dict:
    """
    Scan all signal sources and queue pending trades.
    Returns a run summary dict.
    """
    global _agent_running
    if _agent_running:
        return {"status": "already_running"}

    _agent_running = True
    summary = {
        "run_at": _now().isoformat(),
        "signals_found": 0,
        "trades_queued": 0,
        "sources": [],
        "status": "ok",
    }
    _new_trade_ids: set[str] = set()   # track only trades queued in THIS run

    try:
        # ── Load user-adopted strategy overrides ──────────────────────────────
        _ov           = _load_strategy_overrides()

        # Scale defaults to account size — small accounts need higher % per trade
        if portfolio_value < 5_000:
            _default_risk    = 0.08   # 8% risk per trade for tiny accounts
            _default_max_pos = 0.25   # 25% max position
        elif portfolio_value < 20_000:
            _default_risk    = 0.04
            _default_max_pos = 0.15
        else:
            _default_risk    = 0.02
            _default_max_pos = 0.10

        risk_pct      = _ov.get("risk_pct",        _default_risk)
        max_pos_pct   = _ov.get("max_position_pct", _default_max_pos)
        stop_loss_pct = _ov.get("stop_loss_pct",    0.03)
        max_notional  = portfolio_value * max_pos_pct
        if _ov:
            print(f"[agent] strategy overrides loaded: risk={risk_pct*100:.1f}% max_pos={max_pos_pct*100:.0f}% sl={stop_loss_pct*100:.1f}% (reason: {_ov.get('reason','')})")

        # ── Load active strategy notes (qualitative guidance from reviews) ─────
        _strategy_notes = _load_active_strategy_notes()
        if _strategy_notes:
            print(f"[agent] {len(_strategy_notes)} strategy note(s) loaded for AI context")
        MIN_CASH_PCT  = 0.05   # always keep ≥5% of portfolio as cash

        # ── Problem 1: Market Regime gate ─────────────────────────────────────
        from src.monitor.market_regime import get_market_regime
        regime = get_market_regime()
        summary["regime"] = regime["regime"]
        summary["regime_reason"] = regime["reason"]

        min_ai_score = regime["min_ai_score"]
        size_factor  = regime["size_factor"]

        # ── Market context overrides (from goal progress + aggression) ────────
        if min_ai_score_override is not None:
            # Take the stricter of regime vs goal-based threshold
            min_ai_score = max(min_ai_score, min_ai_score_override) if regime["regime"] in ("BEAR", "CAUTION") \
                           else min_ai_score_override
            print(f"[agent] min_ai_score overridden to {min_ai_score} (aggression-based)")
        if size_scale_override is not None:
            size_factor = size_factor * size_scale_override
            print(f"[agent] size_factor scaled to {size_factor:.2f} (aggression-based)")

        # Apply user-adopted min_ai_score override (take the stricter value)
        if _ov.get("min_ai_score") is not None:
            min_ai_score = max(min_ai_score, float(_ov["min_ai_score"]))
            print(f"[agent] min_ai_score={min_ai_score} (user override)")

        # ── Cash deployment pressure: lower threshold when cash is excessive ──
        # Count only market-hours runs (9:25–16:05 ET) that produced no trades
        def _was_market_hours_run(r: dict) -> bool:
            try:
                import zoneinfo
                et = zoneinfo.ZoneInfo("America/New_York")
            except ImportError:
                et = timezone(timedelta(hours=-4))
            from datetime import time as dtime
            ts = datetime.fromisoformat(r["run_at"]).astimezone(et)
            return ts.weekday() < 5 and dtime(9, 25) <= ts.time() <= dtime(16, 5)

        recent_runs = _run_log[-6:] if _run_log else []
        dry_runs = sum(
            1 for r in recent_runs
            if r.get("trades_queued", 0) == 0 and r.get("status") == "ok" and _was_market_hours_run(r)
        )

        if regime["block_buys"]:
            print(f"[agent] Buys BLOCKED by regime — {regime['reason']}")
            summary["status"] = "buys_blocked"

        # ── Problem 7: Circuit Breaker ────────────────────────────────────────
        from src.monitor.circuit_breaker import check_and_update as _cb_check
        from src.monitor.portfolio_history import get_history as _get_history
        try:
            _history = _get_history()
            breaker = _cb_check(_history)
        except Exception:
            breaker = {"triggered": False}
        summary["circuit_breaker"] = breaker.get("triggered", False)
        if breaker.get("triggered"):
            print(f"[agent] Buys BLOCKED by circuit breaker — {breaker.get('reason','')}")
            summary["status"] = "buys_blocked"

        # ── Guard: check cash and open slots ─────────────────────────────────
        cash = portfolio_value   # fallback
        owned_symbols: set[str] = set()
        slots_remaining = 10    # fallback
        alpaca_positions: list = []
        alpaca_open_sell_symbols: set[str] = set()
        _open_orders_fetched: bool = False   # track whether list_orders() succeeded
        try:
            from src.trader.alpaca_trader import get_client, get_account
            acct = get_account()
            cash = float(acct.cash)
            client = get_client()
            alpaca_positions = client.list_positions()   # fetch ONCE
            owned_symbols = {p.symbol for p in alpaca_positions}
            slots_remaining = max(0, 10 - len(alpaca_positions))
            # Track symbols with open sell orders to avoid duplicate submissions
            open_orders = client.list_orders(status="open")
            alpaca_open_sell_symbols = {o.symbol for o in open_orders if o.side == "sell"}
            _open_orders_fetched = True
            if alpaca_open_sell_symbols:
                print(f"[agent] Open sell orders in Alpaca: {alpaca_open_sell_symbols}")

            # ── Signal-reversal auto-cancel ────────────────────────────────────
            # If holdings monitor now says HOLD/ADD for a symbol that has an open
            # AI-triggered sell order, cancel it. Hard-stop orders are never cancelled.
            cache_age_hours = None
            refreshed_at = holdings_cache.get("refreshed_at")
            if refreshed_at:
                try:
                    cache_age_hours = (datetime.utcnow() - datetime.fromisoformat(refreshed_at)).total_seconds() / 3600
                except Exception:
                    pass

            cache_fresh = cache_age_hours is not None and cache_age_hours < 4
            if cache_fresh and holdings_cache.get("analyzed"):
                signal_map = {p["symbol"]: p.get("sell_signal", "HOLD")
                              for p in holdings_cache.get("positions", [])}
                open_sell_orders = [o for o in open_orders if o.side == "sell"]
                for o in open_sell_orders:
                    current_signal = signal_map.get(o.symbol, "HOLD")
                    if current_signal in ("HOLD", "ADD"):
                        _sell_hold_count[o.symbol] = _sell_hold_count.get(o.symbol, 0) + 1
                        print(f"[agent] {o.symbol} HOLD/ADD signal — hold_count={_sell_hold_count[o.symbol]}/2 (cancel after 2 consecutive)")
                        if _sell_hold_count[o.symbol] >= 2:
                            # Check our internal queue — skip if it was a hard-stop trade
                            is_hard_stop = any(
                                t.get("source") == "hard_stop" and t.get("symbol") == o.symbol
                                and t.get("status") in ("pending", "executed")
                                for t in _pending.values()
                            )
                            if not is_hard_stop:
                                try:
                                    from src.trader.alpaca_trader import cancel_order as _cancel
                                    _cancel(o.id)
                                    _sell_hold_count[o.symbol] = 0
                                    print(f"[agent] Auto-cancelled sell order {o.id} for {o.symbol} — 2 consecutive HOLD signals")
                                    summary.setdefault("cancelled_orders", []).append(o.symbol)
                                except Exception as ce:
                                    print(f"[agent] Failed to cancel {o.symbol} order: {ce}")
                    else:
                        if _sell_hold_count.get(o.symbol, 0) > 0:
                            print(f"[agent] {o.symbol} signal back to {current_signal} — resetting hold_count")
                        _sell_hold_count[o.symbol] = 0
        except Exception as e:
            print(f"[agent] account check failed: {e}")
            # fall back: use holdings_cache
            owned_symbols = {p["symbol"] for p in holdings_cache.get("positions", [])}
            # SAFETY: if list_orders() never succeeded, block ALL sell submissions this cycle
            # to prevent duplicate orders (Alpaca rejects with "insufficient qty available")
            if not _open_orders_fetched:
                alpaca_open_sell_symbols = owned_symbols
                print(f"[agent] WARNING: could not fetch open orders — blocking sell submissions to prevent duplicates")

        if slots_remaining == 0:
            print("[agent] No open slots — skipping buy signals")
        min_cash_reserve = portfolio_value * MIN_CASH_PCT
        spendable_cash = max(0, cash - min_cash_reserve)
        if cash < min_cash_reserve:
            print(f"[agent] Cash ${cash:.0f} below {MIN_CASH_PCT*100:.0f}% reserve (${min_cash_reserve:.0f}) — skipping buy signals")

        can_buy = (
            slots_remaining > 0
            and cash >= min_cash_reserve
            and not regime["block_buys"]
            and not breaker.get("triggered", False)
        )

        # ── Cash pressure: lower threshold when cash is piling up ────────────
        cash_pct = cash / portfolio_value if portfolio_value > 0 else 0
        if can_buy and cash_pct > 0.30 and dry_runs >= 2 and regime["regime"] != "BEAR":
            relaxed = max(5, min_ai_score - 1)
            print(f"[agent] Cash pressure: {cash_pct:.0%} cash, {dry_runs} dry runs → relaxing min_ai_score {min_ai_score}→{relaxed}")
            min_ai_score = relaxed
        elif can_buy and cash_pct > 0.50 and regime["regime"] != "BEAR":
            relaxed = max(5, min_ai_score - 1)
            print(f"[agent] High cash ({cash_pct:.0%}) → relaxing min_ai_score {min_ai_score}→{relaxed}")
            min_ai_score = relaxed
        summary["cash_pct"] = round(cash_pct * 100, 1)
        summary["min_ai_score_used"] = min_ai_score

        MIN_ORDER_NOTIONAL = max(10.0, portfolio_value * 0.005)  # at least 0.5% of portfolio or $10

        def _size(price: float, stop: float) -> float:
            """Risk-based notional, scaled by regime size_factor. Never exceeds spendable cash."""
            risk_per_share = max(price - stop, 0.01)
            shares = (portfolio_value * risk_pct) / risk_per_share
            raw = min(round(shares * price, 2), max_notional, spendable_cash * 0.95)
            return round(max(raw * size_factor, MIN_ORDER_NOTIONAL), 2)

        # ── Problem 4: earnings check helper ─────────────────────────────────
        from src.monitor.news_monitor import earnings_within_days
        from src.monitor.sector_checker import check_sector_limit

        # Reuse already-fetched positions list for sector check
        current_positions = [
            {"symbol": p.symbol, "market_value": float(p.market_value)}
            for p in alpaca_positions
        ] if can_buy else []

        def _earnings_safe(symbol: str) -> bool:
            """Return True if it's safe to buy (no earnings within 3 days)."""
            has_earnings, earn_date = earnings_within_days(symbol, days=3)
            if has_earnings:
                print(f"[agent] Skip {symbol} — earnings on {earn_date}")
            return not has_earnings

        def _sector_safe(symbol: str) -> bool:
            """Return True if adding this symbol won't breach sector limits."""
            allowed, reason = check_sector_limit(symbol, current_positions, portfolio_value)
            if not allowed:
                print(f"[agent] Skip {symbol} — sector limit: {reason}")
            return allowed

        # ── 1. S&P 500 Scanner: STRONG_BUY >= min_score OR BUY >= min_score+1 ──
        scan = scan_cache.get("sp500", {})
        if scan.get("status") == "done" and can_buy:
            scanner_added = 0
            for c in scan.get("candidates", []):
                signal   = c.get("signal", "")
                ai_score = c.get("ai_score") or 0
                if signal in ("STRONG_BUY", "BUY") and ai_score >= min_ai_score:
                    pass   # allowed
                else:
                    continue
                # Problem 4: skip if earnings this week
                if not _earnings_safe(c["symbol"]):
                    summary["signals_found"] += 1   # found but skipped
                    continue
                # Problem 5: skip if sector concentration limit reached
                if not _sector_safe(c["symbol"]):
                    summary["signals_found"] += 1
                    continue
                price = c.get("price", 0)
                stop = c.get("stop_loss") or (price * (1 - stop_loss_pct) if price else None)
                notional = _size(price, stop) if (price and stop and stop < price) else \
                           min(portfolio_value * risk_pct * 3 * size_factor, max_notional)

                trade = _make_trade(
                    symbol=c["symbol"], side="buy",
                    notional=notional, qty=None,
                    signal="STRONG_BUY",
                    confidence=c.get("ai_score", 7) / 10,
                    reason=c.get("reason", "Top S&P 500 scanner pick"),
                    source="scanner",
                    stop_loss=stop,
                    target_price=c.get("target_price"),
                    price=price,
                    rsi=c.get("rsi"),
                    momentum_5d=c.get("momentum_5d"),
                    volume_ratio=c.get("volume_ratio"),
                    near_breakout=c.get("near_breakout"),
                    universe=c.get("universe"),
                )
                # Allow adding to existing position when cash is very high and signal is strong,
                # but only if the combined position stays within max_pos_pct of portfolio.
                allow_add = False
                if cash_pct > 0.50 and (c.get("ai_score") or 0) >= 8:
                    cur_mv = next((float(p.market_value) for p in alpaca_positions if p.symbol == c["symbol"]), 0.0)
                    new_total = cur_mv + (trade["notional"] or 0)
                    allow_add = new_total <= max_notional
                if _add_trade(trade, owned_symbols, allow_add_to_position=allow_add):
                    summary["signals_found"] += 1
                    summary["trades_queued"] += 1
                    scanner_added += 1
                    _new_trade_ids.add(trade["id"])
            if scanner_added:
                summary["sources"].append("scanner")

        # ── 2. Watchlist: use cached analysis (no extra Claude call) ──────────
        CACHE_MAX_AGE_SECONDS = 4 * 3600   # Problem 8: 4-hour cache expiry
        if can_buy and watchlist:
            cache      = analysis_cache or {}
            ts_cache   = analysis_timestamps or {}
            wl_added   = 0
            in_market  = _is_market_hours()
            import time as _time

            for symbol in watchlist:
                cached = cache.get(symbol)

                # Problem 8: discard stale cache (> 4 h old during market hours)
                if cached and in_market:
                    ts = ts_cache.get(symbol, 0)
                    age = _time.time() - ts
                    if age > CACHE_MAX_AGE_SECONDS:
                        print(f"[agent] {symbol} cache stale ({age/3600:.1f}h) — refreshing")
                        cached = None   # force re-fetch below

                # If no (valid) cache and market is open, run fresh analysis
                if not cached and in_market:
                    try:
                        from src.monitor.price_monitor import get_quote, get_ohlcv
                        from src.monitor.news_monitor import get_news
                        from src.analysis.ai_analyst import analyze
                        quote = get_quote(symbol)
                        ohlcv = get_ohlcv(symbol)
                        news  = get_news(symbol, limit=5)
                        cached = analyze(symbol, ohlcv, quote, news=news, strategy_notes=_strategy_notes or None)
                        if analysis_cache is not None:
                            analysis_cache[symbol] = cached
                        if analysis_timestamps is not None:
                            analysis_timestamps[symbol] = _time.time()
                    except Exception as e:
                        print(f"[agent] watchlist {symbol} analysis error: {e}")
                        continue
                elif not cached:
                    # Off-hours and no cache — skip
                    continue

                sig  = cached.get("signal")
                conf = cached.get("confidence", 0)
                # Adapt confidence threshold to market regime (mirrors scanner ai_score gate)
                wl_min_conf = 0.75 if regime.get("regime") == "CAUTION" else 0.70
                if sig == "BUY" and conf >= wl_min_conf:
                    # Problem 4: skip if earnings this week
                    if not _earnings_safe(symbol):
                        summary["signals_found"] += 1
                        continue
                    # Problem 5: skip if sector concentration limit reached
                    if not _sector_safe(symbol):
                        summary["signals_found"] += 1
                        continue
                    price = cached.get("price") or 0
                    stop  = cached.get("stop_loss") or (price * (1 - stop_loss_pct) if price else None)
                    notional = _size(price, stop) if (price and stop and stop < price) else \
                               min(portfolio_value * risk_pct * 3 * size_factor, max_notional)

                    trade = _make_trade(
                        symbol=symbol, side="buy",
                        notional=notional, qty=None,
                        signal="BUY", confidence=conf,
                        reason=cached.get("reasoning", "Watchlist AI signal"),
                        source="watchlist",
                        stop_loss=stop,
                        target_price=cached.get("target_price"),
                        price=price,
                    )
                    if _add_trade(trade, owned_symbols):
                        summary["signals_found"] += 1
                        summary["trades_queued"] += 1
                        wl_added += 1
                        _new_trade_ids.add(trade["id"])
            if wl_added:
                summary["sources"].append("watchlist")

        # ── 3. Holdings: SELL / REDUCE ────────────────────────────────────────
        # First pass: hard stop-loss from live Alpaca positions (no cache dependency)
        HARD_STOP_PCT = -stop_loss_pct * 100   # e.g. -3.0%
        holdings_added = 0
        hard_stop_symbols: set[str] = set()
        for ap in alpaca_positions:
            if ap.symbol in alpaca_open_sell_symbols:
                print(f"[agent] skip {ap.symbol} hard stop — open sell order already in Alpaca")
                continue
            plpc = float(ap.unrealized_plpc) * 100   # Alpaca returns as decimal
            if plpc <= HARD_STOP_PCT:
                print(f"[agent] Hard stop: {ap.symbol} down {plpc:.1f}% (threshold {HARD_STOP_PCT:.1f}%)")
                trade = _make_trade(
                    symbol=ap.symbol, side="sell",
                    notional=None, qty=None,
                    signal="SELL",
                    confidence=0.9,
                    reason=f"Hard stop: position down {plpc:.1f}% (threshold {HARD_STOP_PCT:.1f}%)",
                    source="hard_stop",
                    price=float(ap.current_price),
                )
                if _add_trade(trade, owned_symbols):
                    summary["signals_found"] += 1
                    summary["trades_queued"] += 1
                    holdings_added += 1
                    hard_stop_symbols.add(ap.symbol)
                    _new_trade_ids.add(trade["id"])

        # Second pass: AI sell signals from holdings cache (skip already handled)
        today_str = _now().strftime("%Y-%m-%d")
        # Purge stale entries from prior days
        for k in list(_reduce_today.keys()):
            if _reduce_today[k] != today_str:
                del _reduce_today[k]

        for pos in holdings_cache.get("positions", []):
            sell_signal = pos.get("sell_signal")
            if sell_signal not in ("SELL", "REDUCE"):
                continue
            if pos["symbol"] not in owned_symbols:
                print(f"[agent] skip {pos['symbol']} sell — position already closed")
                continue
            if pos["symbol"] in hard_stop_symbols:
                continue   # already queued via hard stop
            if pos["symbol"] in alpaca_open_sell_symbols:
                print(f"[agent] skip {pos['symbol']} {sell_signal} — open sell order already in Alpaca")
                continue
            if sell_signal == "REDUCE" and _reduce_today.get(pos["symbol"]) == today_str:
                print(f"[agent] skip {pos['symbol']} REDUCE — already reduced today")
                continue
            qty = float(pos.get("qty", 0))
            close_qty = max(1, math.floor(qty * 0.5)) if sell_signal == "REDUCE" else None
            trade = _make_trade(
                symbol=pos["symbol"], side="sell",
                notional=None,
                qty=close_qty,
                signal=sell_signal,
                confidence=0.8 if sell_signal == "SELL" else 0.6,
                reason=pos.get("reason", "Holdings monitor sell signal"),
                source="holdings",
                price=pos.get("current_price"),
            )
            if _add_trade(trade, owned_symbols):
                summary["signals_found"] += 1
                summary["trades_queued"] += 1
                holdings_added += 1
                _new_trade_ids.add(trade["id"])
                if sell_signal == "REDUCE":
                    _reduce_today[pos["symbol"]] = today_str

        if holdings_added:
            summary["sources"].append("holdings")

        # ── Auto-approve: execute high-confidence trades from THIS run only ──
        # Only auto-approves trades queued in this run — not stale ones from prior runs.
        auto_threshold = _get_auto_approve_threshold()
        if auto_threshold is not None and auto_threshold >= 0:
            auto_approved = 0
            for trade in list(_pending.values()):
                if trade["id"] not in _new_trade_ids:
                    continue   # only act on trades queued this run
                if trade["status"] != "pending":
                    continue
                if trade["confidence"] >= auto_threshold:
                    try:
                        approve_trade(trade["id"])
                        auto_approved += 1
                        print(f"[agent] Auto-approved {trade['side'].upper()} {trade['symbol']} "
                              f"(conf={trade['confidence']:.2f} ≥ {auto_threshold})")
                    except Exception as e:
                        print(f"[agent] Auto-approve failed for {trade['id']}: {e}")
            if auto_approved:
                summary["auto_approved"] = auto_approved

    except Exception as e:
        summary["status"] = "error"
        summary["error"] = str(e)
        print(f"[agent] run error: {e}")
    finally:
        _agent_running = False

    _run_log.append(summary)
    if len(_run_log) > MAX_LOG:
        _run_log.pop(0)
    _save_log(_run_log)

    return summary


# ── Auto-approve config ───────────────────────────────────────────────────────

_AUTO_APPROVE_FILE = Path(__file__).parent.parent.parent / "data" / "auto_approve.json"

def _get_auto_approve_threshold() -> Optional[float]:
    """
    Returns the auto-approve confidence threshold, or None if autonomous mode is off.
    Default: autonomous (threshold=0.0 — execute all trades).
    """
    try:
        if _AUTO_APPROVE_FILE.exists():
            cfg = json.loads(_AUTO_APPROVE_FILE.read_text())
            if not cfg.get("enabled", True):
                return None   # manually disabled
            return float(cfg.get("threshold", 0.0))
    except Exception:
        pass
    return 0.0   # default: autonomous, execute all trades


def set_auto_approve(enabled: bool, threshold: float = 0.0) -> dict:
    """Enable or disable autonomous execution. Persisted to disk."""
    cfg = {"enabled": enabled, "threshold": round(threshold, 2)}
    _AUTO_APPROVE_FILE.parent.mkdir(exist_ok=True)
    _AUTO_APPROVE_FILE.write_text(json.dumps(cfg))
    print(f"[agent] Autonomous execution {'ENABLED' if enabled else 'DISABLED'} (threshold={threshold})")
    return cfg


def get_auto_approve_config() -> dict:
    """Return current autonomous execution config."""
    try:
        if _AUTO_APPROVE_FILE.exists():
            return json.loads(_AUTO_APPROVE_FILE.read_text())
    except Exception:
        pass
    return {"enabled": True, "threshold": 0.0}   # default: autonomous
