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

_pending: dict[str, dict] = _load_from_disk()
_run_log: list[dict] = []
_agent_running: bool = False

PENDING_TTL_HOURS = 4     # increased from 2h — more time to review pre-market signals
ERROR_TTL_HOURS   = 24    # auto-clear error trades after 24 h
MAX_LOG = 20


def _now() -> datetime:
    return datetime.now(timezone.utc)


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
        "status": "pending",
        "created_at": now.isoformat(),
        "expires_at": (now + timedelta(hours=PENDING_TTL_HOURS)).isoformat(),
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


def _add_trade(trade: dict, existing_symbols: set[str]) -> bool:
    """
    Add trade to queue if no duplicate pending for same symbol+side.
    `existing_symbols` = symbols we already own (skip buy if already held).
    Returns True if added.
    """
    sym = trade["symbol"]
    side = trade["side"]

    # Don't buy what we already own
    if side == "buy" and sym in existing_symbols:
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

def approve_trade(trade_id: str) -> dict:
    """Approve and execute. Places bracket order when stop/target available."""
    trade = _pending.get(trade_id)
    if not trade:
        raise ValueError(f"Trade {trade_id} not found")
    if trade["status"] != "pending":
        raise ValueError(f"Trade {trade_id} is {trade['status']}, not pending")
    if datetime.fromisoformat(trade["expires_at"]) < _now():
        trade["status"] = "expired"
        _save_to_disk(_pending)
        raise ValueError(f"Trade {trade_id} has expired")

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

    try:
        risk_pct      = 0.02
        max_notional  = portfolio_value * 0.10

        # ── Problem 1: Market Regime gate ─────────────────────────────────────
        from src.monitor.market_regime import get_market_regime
        regime = get_market_regime()
        summary["regime"] = regime["regime"]
        summary["regime_reason"] = regime["reason"]

        min_ai_score = regime["min_ai_score"]
        size_factor  = regime["size_factor"]

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
        try:
            from src.trader.alpaca_trader import get_client, get_account
            acct = get_account()
            cash = float(acct.cash)
            alpaca_positions = get_client().list_positions()   # fetch ONCE
            owned_symbols = {p.symbol for p in alpaca_positions}
            slots_remaining = max(0, 10 - len(alpaca_positions))
        except Exception as e:
            print(f"[agent] account check failed: {e}")
            # fall back: use holdings_cache
            owned_symbols = {p["symbol"] for p in holdings_cache.get("positions", [])}

        if slots_remaining == 0:
            print("[agent] No open slots — skipping buy signals")
        if cash < 500:
            print(f"[agent] Low cash (${cash:.0f}) — skipping buy signals")

        can_buy = (
            slots_remaining > 0
            and cash >= 500
            and not regime["block_buys"]
            and not breaker.get("triggered", False)
        )

        def _size(price: float, stop: float) -> float:
            """Risk-based notional, scaled by regime size_factor."""
            risk_per_share = max(price - stop, 0.01)
            shares = (portfolio_value * risk_pct) / risk_per_share
            raw = min(round(shares * price, 2), max_notional, cash * 0.95)
            return round(raw * size_factor, 2)

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

        # ── 1. S&P 500 Scanner: STRONG_BUY, ai_score >= regime threshold ─────
        scan = scan_cache.get("sp500", {})
        if scan.get("status") == "done" and can_buy:
            scanner_added = 0
            for c in scan.get("candidates", []):
                # Use regime-adjusted minimum score
                if not (c.get("signal") == "STRONG_BUY" and (c.get("ai_score") or 0) >= min_ai_score):
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
                stop = c.get("stop_loss") or (price * 0.97 if price else None)
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
                )
                if _add_trade(trade, owned_symbols):
                    summary["signals_found"] += 1
                    summary["trades_queued"] += 1
                    scanner_added += 1
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
                        cached = analyze(symbol, ohlcv, quote, news=news)
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
                if sig == "BUY" and conf >= 0.7:
                    # Problem 4: skip if earnings this week
                    if not _earnings_safe(symbol):
                        summary["signals_found"] += 1
                        continue
                    # Problem 5: skip if sector concentration limit reached
                    if not _sector_safe(symbol):
                        summary["signals_found"] += 1
                        continue
                    price = cached.get("price") or 0
                    stop  = cached.get("stop_loss") or (price * 0.97 if price else None)
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
            if wl_added:
                summary["sources"].append("watchlist")

        # ── 3. Holdings: SELL / REDUCE ────────────────────────────────────────
        positions = holdings_cache.get("positions", [])
        holdings_added = 0
        for pos in positions:
            sell_signal = pos.get("sell_signal")
            if sell_signal not in ("SELL", "REDUCE"):
                continue
            qty = float(pos.get("qty", 0))
            if sell_signal == "REDUCE":
                close_qty = max(1, math.floor(qty * 0.5))
            else:
                close_qty = None   # close entire position

            trade = _make_trade(
                symbol=pos["symbol"], side="sell",
                notional=None,
                qty=close_qty if sell_signal == "REDUCE" else None,
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

        if holdings_added:
            summary["sources"].append("holdings")

        # ── Auto-approve: execute high-confidence trades without human review ──
        auto_threshold = _get_auto_approve_threshold()
        if auto_threshold is not None and auto_threshold > 0:
            auto_approved = 0
            for trade in list(_pending.values()):
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

    return summary


# ── Auto-approve config ───────────────────────────────────────────────────────

_AUTO_APPROVE_FILE = Path(__file__).parent.parent.parent / "data" / "auto_approve.json"

def _get_auto_approve_threshold() -> Optional[float]:
    """
    Returns the auto-approve confidence threshold, or None if disabled.
    Config stored in data/auto_approve.json: {"enabled": true, "threshold": 0.80}
    """
    try:
        if _AUTO_APPROVE_FILE.exists():
            cfg = json.loads(_AUTO_APPROVE_FILE.read_text())
            if cfg.get("enabled") and cfg.get("threshold"):
                return float(cfg["threshold"])
    except Exception:
        pass
    return None  # disabled by default


def set_auto_approve(enabled: bool, threshold: float = 0.80) -> dict:
    """Enable or disable auto-approve. Persisted to disk."""
    cfg = {"enabled": enabled, "threshold": round(threshold, 2)}
    _AUTO_APPROVE_FILE.parent.mkdir(exist_ok=True)
    _AUTO_APPROVE_FILE.write_text(json.dumps(cfg))
    status = f"Auto-approve {'ENABLED' if enabled else 'DISABLED'} (threshold={threshold})"
    print(f"[agent] {status}")
    return cfg


def get_auto_approve_config() -> dict:
    """Return current auto-approve config."""
    try:
        if _AUTO_APPROVE_FILE.exists():
            return json.loads(_AUTO_APPROVE_FILE.read_text())
    except Exception:
        pass
    return {"enabled": False, "threshold": 0.80}
