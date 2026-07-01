"""
Trade Agent (Rex) — v8 纯机械执行 + 持久化待执行队列。

买入:扫描候选(已过趋势门 + 按动量排名)→ 财报门 + 止损门(-8%,+0.5%容差)→
       固定 confidence 0.8 自动执行(无 AI 分门、无 Track1/2、无自选特殊路径)。
卖出:holdings_monitor 的机械 SELL(-8%止损 / 追踪止盈 +6%/-8% / MA20破位);
       主动卖出前先取消保护止损单再平仓。无 REDUCE/AI 软清仓。
仓位:regime 决定上限 + size_factor 缩放;入场价格漂移门在执行端。

基础设施:待执行队列落盘(trades.json,重启不丢);已持仓去重;现金/槽位校验;
       bracket 单(入场+止损);error 单 24h 自动清理。
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
_sell_hold_count: dict[str, int] = {}  # symbol -> consecutive HOLD count; cancel only after >= 2

ERROR_TTL_HOURS    = 24   # auto-clear error trades after 24 h
REJECTED_TTL_HOURS = 48   # auto-clear rejected trades after 48 h
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
    """True between 9:31 AM and 4:05 PM US/Eastern Mon-Fri.
    Starts at 9:31 (not 9:30) to avoid the first-minute liquidity chaos:
    wide spreads, erratic vol_ratio, and unreliable price signals.
    Scanner cascade also runs at 9:31 AM, so buy gate and scan are naturally synced.
    """
    from datetime import time as dtime
    try:
        import zoneinfo
        et = zoneinfo.ZoneInfo("America/New_York")
    except ImportError:
        et = timezone(timedelta(hours=-4))   # EDT approximation

    now_et = _now().astimezone(et)
    if now_et.weekday() >= 5:   # Saturday / Sunday
        return False
    return dtime(9, 31) <= now_et.time() <= dtime(16, 5)


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
    screen_track: Optional[str] = None,
    veto: bool = False,
    veto_reason: Optional[str] = None,
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
        "screen_track": screen_track,
        "veto": veto,                 # AI 排雷:True → 走人工审核(不自动执行)
        "veto_reason": veto_reason,
        "status": "pending",
        "created_at": now.isoformat(),
        # veto 买单(进人工审核队列)只留 2h —— 未处理即作废释放槽位补位(C 方案);
        # 其他单(自动买入极少滞留、卖出)沿用"下次收盘"过期。
        "expires_at": ((now + timedelta(hours=2)).isoformat()
                       if (side == "buy" and veto) else _next_session_close().isoformat()),
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
            age = (now - datetime.fromisoformat(t["created_at"])).total_seconds()
            if age > ERROR_TTL_HOURS * 3600:
                del _pending[t["id"]]
                changed = True
        # Auto-clear rejected trades older than 48 h
        if t["status"] == "rejected":
            age = (now - datetime.fromisoformat(t["created_at"])).total_seconds()
            if age > REJECTED_TTL_HOURS * 3600:
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


# ── Slot & drift helpers(纯函数,便于单测)──────────────────────────────────

def _slots_remaining(max_positions: int, num_open_positions: int, num_pending_buys: int) -> int:
    """可买槽位 = 上限 − 持仓 − 已 pending 买单。
    pending 买单也占位 —— 否则两次扫描各自算"还有空位"叠加填满 → 超额(6-30 暴冲根因)。"""
    return max(0, max_positions - num_open_positions - num_pending_buys)


# ── Approve / Reject ──────────────────────────────────────────────────────────

PRICE_DRIFT_THRESHOLD = 0.015  # 1.5% — if price moved >1.5% from scan, signal is stale


def _price_drift_decision(scan_price: float, live_price: float) -> tuple[str, float]:
    """买入执行前的价格漂移判定。返回 (decision, drift)。
    - drift > +1.5%(涨太多=追高)→ "reject"(信号失效)
    - drift ≤ +1.5%(含跌了=更好入场)→ "proceed"
    drift 为小数(0.025 = +2.5%)。"""
    drift = (live_price - scan_price) / scan_price
    return ("reject" if drift > PRICE_DRIFT_THRESHOLD else "proceed"), drift


def approve_trade(trade_id: str) -> dict:
    """Approve and execute. Places bracket order when stop/target available.
    买入执行前校验价格漂移 ≤ 1.5%(涨超即拒,信号失效;跌了放行=更好入场)。"""
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
            from src.monitor.price_monitor import get_quote as _gq
            live_price = (_gq(trade["symbol"]) or {}).get("price") or 0   # Alpaca(替代 yfinance)
            scan_price = trade["price"]
            if live_price > 0 and scan_price > 0:
                decision, drift = _price_drift_decision(scan_price, live_price)
                trade["price_at_approve"] = round(live_price, 2)
                trade["price_drift_pct"]  = round(drift * 100, 2)
                if decision == "reject":
                    # Price ran up too much — stale signal, auto-reject
                    trade["status"] = "rejected"
                    trade["error"]  = (
                        f"价格漂移 +{drift*100:.1f}% (扫描价 ${scan_price:.2f} → 现价 ${live_price:.2f})，"
                        f"超过 {PRICE_DRIFT_THRESHOLD*100:.1f}% 阈值，信号已失效"
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
        if trade["side"] == "sell":
            # 主动卖出前,先取消该股挂着的保护止损/任何 sell 单,释放被占用的股数,
            # 否则 close_position / 卖单会被 Alpaca 拒("insufficient qty available")。
            # 关键:取消后要等 Alpaca 真正释放股数再卖,否则有竞态(取消未落地→insufficient qty)。
            try:
                import time as _time
                from src.trader.alpaca_trader import get_client as _gc, cancel_order as _cxl
                _client = _gc()
                _cancelled = []
                for _o in _client.list_orders(status="open"):
                    if _o.symbol == trade["symbol"] and _o.side == "sell":
                        _cxl(_o.id)
                        _cancelled.append(_o.id)
                        print(f"[agent] cancelled resting sell order {_o.id} for {trade['symbol']} before close")
                # 轮询确认取消落地(被占股数已释放),最多等 ~3s
                for _ in range(10):
                    if not _cancelled:
                        break
                    _still = {o.id for o in _client.list_orders(status="open")
                              if o.symbol == trade["symbol"] and o.side == "sell"}
                    if not (set(_cancelled) & _still):
                        break
                    _time.sleep(0.3)
            except Exception as _ce:
                print(f"[agent] cancel-before-sell failed for {trade['symbol']}: {_ce}")

        if trade["side"] == "sell" and trade["qty"] is None:
            # 兜底:若仍偶发 insufficient qty(取消刚落地),短暂重试一次
            try:
                order = close_position(trade["symbol"])
            except Exception as _se:
                if "insufficient qty" in str(_se).lower():
                    import time as _time2
                    _time2.sleep(1.0)
                    order = close_position(trade["symbol"])
                else:
                    raise
        elif (
            trade["side"] == "buy"
            and trade.get("stop_loss")
            and trade.get("price")
        ):
            # Bracket order: market entry + stop-loss only (trailing stop handles upside exit)
            order = place_order(
                symbol=trade["symbol"],
                side="buy",
                notional=trade["notional"],
                qty=trade["qty"],
                order_type="market",
                stop_loss=trade["stop_loss"],
                take_profit=None,
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
    trade = dict(trade)
    trade["status"] = "rejected"
    del _pending[trade_id]
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
    size_scale_override: Optional[float] = None,  # from market_context aggression(仅缩放仓位)
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

    # ── 剪枝:过期 / 跨日孤儿 pending 买单 ─────────────────────────────────────
    # 作废两类 pending 买单,释放其占用的槽位(否则膨胀队列 + 欠配 + 曾致暴冲):
    #  ① 过期:expires_at 已过 —— 含 veto 买单 2h 未处理(C 方案)、超"下次收盘"的滞留单;
    #  ② 跨日孤儿:非当日 ET 排的(崩溃中途 queue、_new_trade_ids 丢失、永不自动批)。
    # 槽位在下面按 status=="pending" 计算,这里先作废 → 本轮买入循环即可补位。
    try:
        import zoneinfo as _zi_p
        _et_p = _zi_p.ZoneInfo("America/New_York")
        _today_p = _now().astimezone(_et_p).date()
        _now_p = _now()
        _stale_ids = [
            t["id"] for t in _pending.values()
            if t.get("side") == "buy" and t.get("status") == "pending"
            and (
                datetime.fromisoformat(t["expires_at"]) < _now_p                              # ① 过期(含 veto 2h)
                or datetime.fromisoformat(t["created_at"]).astimezone(_et_p).date() != _today_p  # ② 跨日孤儿
            )
        ]
        for _sid in _stale_ids:
            _pending[_sid]["status"] = "expired"
        if _stale_ids:
            _save_to_disk(_pending)
            print(f"[agent] 剪掉 {len(_stale_ids)} 个过期/跨日 pending 买单(作废,含 veto 2h 未处理)→ 释放槽位补位")
    except Exception as _pe:
        print(f"[agent] pending-prune skipped: {_pe}")

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
        stop_loss_pct = _ov.get("stop_loss_pct",    0.08)   # v8: 固定 -8%(原默认 0.03 是老值)
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

        size_factor  = regime["size_factor"]

        # ── Market context size override (aggression-based) — v8 仅按 regime 缩放仓位,不用 AI 分门 ──
        if size_scale_override is not None:
            size_factor = size_factor * size_scale_override
            print(f"[agent] size_factor scaled to {size_factor:.2f} (aggression-based)")

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
        cash: float = 0.0
        equity: float = portfolio_value
        owned_symbols: set[str] = set()
        slots_remaining = regime.get("max_positions", 10)   # fallback respects macro filter
        alpaca_positions: list = []
        alpaca_open_sell_symbols: set[str] = set()
        _open_orders_fetched: bool = False   # track whether list_orders() succeeded
        # 防御性默认:Alpaca 取数失败(SSL/限流)时这些保持有效值,避免后续 cash/None 崩溃 → 安全跳过买入
        cash: float = 0.0
        equity: float = float(portfolio_value or 0)
        positions_market_value: float = 0.0
        owned_symbols: set[str] = {p["symbol"] for p in holdings_cache.get("positions", [])}
        try:
            from src.trader.alpaca_trader import get_client, get_account
            acct = get_account()
            client = get_client()
            alpaca_positions = client.list_positions()   # fetch ONCE
            owned_symbols = {p.symbol for p in alpaca_positions}
            # Use equity-based cash to avoid margin: never spend more than we own
            positions_market_value = sum(float(p.market_value) for p in alpaca_positions)
            equity = float(acct.equity)
            cash = max(0.0, equity - positions_market_value)
            if float(acct.cash) < 0:
                print(f"[agent] ⚠️  Margin detected: acct.cash=${float(acct.cash):,.0f}, "
                      f"equity=${equity:,.0f}, positions=${positions_market_value:,.0f} "
                      f"→ true_cash=${cash:,.0f}")
            regime_max_pos  = regime.get("max_positions", 10)
            # 已 pending 的买单也占用槽位 —— 否则两次扫描各自填满 cap,叠加暴冲(6-30 根因)
            _pending_buy_syms = {t["symbol"] for t in _pending.values()
                                 if t.get("side") == "buy" and t.get("status") == "pending"}
            slots_remaining = _slots_remaining(regime_max_pos, len(alpaca_positions), len(_pending_buy_syms))
            if _pending_buy_syms:
                print(f"[agent] {len(_pending_buy_syms)} 个待审买单占用槽位 → 实际可买 {slots_remaining}")
            if regime_max_pos < 10:
                print(f"[agent] Macro filter: max_positions={regime_max_pos} ({regime['regime']}) "
                      f"— {len(alpaca_positions)} open, {slots_remaining} slots remaining")
            # Track symbols with open sell orders to avoid duplicate submissions
            open_orders = client.list_orders(status="open")

            def _is_protective_stop(o) -> bool:
                ot = str(getattr(o, "order_type", None) or getattr(o, "type", None) or "").lower()
                return ot in ("stop", "stop_limit")

            # 只把"真正的市价/限价平仓单"算作正在卖;入场挂的 -8% 保护止损(stop)不算 ——
            # 否则它会永久挡住 MA20破位/追踪止盈/hard-stop 的主动卖出(close_position 前会先取消止损)。
            alpaca_open_sell_symbols = {
                o.symbol for o in open_orders if o.side == "sell" and not _is_protective_stop(o)
            }
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
                        # Never touch stop / stop_limit orders — those are bracket stop-losses
                        # placed at entry time and must survive independently of AI signals.
                        order_type = getattr(o, "order_type", None) or getattr(o, "type", None) or ""
                        if str(order_type).lower() in ("stop", "stop_limit"):
                            continue
                        _sell_hold_count[o.symbol] = _sell_hold_count.get(o.symbol, 0) + 1
                        print(f"[agent] {o.symbol} HOLD/ADD signal — hold_count={_sell_hold_count[o.symbol]}/2 (cancel after 2 consecutive)")
                        if _sell_hold_count[o.symbol] >= 2:
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

        # Hard gate: no buys outside 9:30 AM – 4:00 PM ET
        _mkt_open = _is_market_hours()
        if not _mkt_open:
            print("[agent] Outside market hours — buy logic disabled, sell/stop monitoring only")

        can_buy = (
            _mkt_open
            and slots_remaining > 0
            and cash >= min_cash_reserve
            and not regime["block_buys"]
            and not breaker.get("triggered", False)
        )

        cash_pct = cash / portfolio_value if portfolio_value > 0 else 0
        summary["cash_pct"] = round(cash_pct * 100, 1)

        MIN_ORDER_NOTIONAL = max(10.0, portfolio_value * 0.005)  # at least 0.5% of portfolio or $10

        def _size(price: float, stop: float) -> float:
            """Risk-based notional, scaled by regime size_factor. Never exceeds spendable cash."""
            risk_per_share = max(price - stop, 0.01)
            shares = (portfolio_value * risk_pct) / risk_per_share
            raw = min(round(shares * price, 2), max_notional, spendable_cash * 0.95)
            return round(max(raw * size_factor, MIN_ORDER_NOTIONAL), 2)

        # ── 财报门:今/明天有财报未公布 → 跳过(已公布的盘后动量允许) ──────────
        from src.monitor.news_monitor import earnings_within_days

        def _earnings_safe(symbol: str) -> bool:
            has_earnings, earn_date = earnings_within_days(symbol, days=1)
            if has_earnings:
                print(f"[agent] Skip {symbol} — earnings on {earn_date} (too close)")
            return not has_earnings

        HARD_STOP_PCT_T1 = 0.08  # v8 固定 -8% 止损上限(+容差,见下)

        # ── v8 选股:扫描候选(已过趋势门 + 按动量排名)→ 机械买入 ──────────────
        scan = scan_cache.get("sp500", {})

        # Stale-signal guard: reject scan from a previous trading day
        _scan_fresh_today = False
        _scanned_at = scan.get("scanned_at")
        if _scanned_at:
            try:
                import zoneinfo as _zi
                _et = _zi.ZoneInfo("America/New_York")
                _scan_date = datetime.fromisoformat(_scanned_at).astimezone(_et).date()
                _today_et  = _now().astimezone(_et).date()
                _scan_fresh_today = (_scan_date == _today_et)
                if not _scan_fresh_today:
                    print(f"[agent] Scan is from {_scan_date} (today={_today_et}) — skipping buys until fresh scan")
            except Exception:
                _scan_fresh_today = False   # conservative: treat unknown as stale

        if scan.get("status") == "done" and can_buy and _scan_fresh_today:
            scanner_added = 0

            for c in list(scan.get("candidates", [])):
                # 槽位上限硬约束:已 queue 满 slots_remaining 就停,绝不超过 regime 仓位 cap。
                # (此前缺这行 → 剩 N 槽却把所有候选全 queue 全自动批 = 6-30 暴冲根因)
                if scanner_added >= slots_remaining:
                    print(f"[agent] 槽位已满(本轮已 queue {scanner_added}/{slots_remaining})— 停止 queue 买单")
                    break
                # v8: 选股=机械动量(scanner 已过趋势门 + 按动量排名)。AI 信号/分数仅作参考、
                # 不挡买入。入场门 = 财报门 + 止损门(+价格漂移门在执行端)。
                if not _earnings_safe(c["symbol"]):
                    summary["signals_found"] += 1   # found but skipped
                    continue
                price = c.get("price", 0)
                stop = c.get("stop_loss") or (price * (1 - stop_loss_pct) if price else None)

                # 止损门:v8 固定 -8% = round(price*0.92,2),取整后距离常微超 8%。加 0.5% 容差,
                # 标准 -8% 放行;真正过宽(>8.5%)才挡。
                if price and stop and stop < price:
                    risk_pct_actual = (price - stop) / price
                    if risk_pct_actual > HARD_STOP_PCT_T1 + 0.005:
                        print(f"[agent] {c['symbol']} skip — stop {risk_pct_actual:.1%} > 8% 上限")
                        summary["signals_found"] += 1
                        continue

                notional = _size(price, stop) if (price and stop and stop < price) else \
                           min(portfolio_value * risk_pct * 3 * size_factor, max_notional)

                # WSB extreme hype → halve position (retail frenzy = late-entry risk)
                wsb_label = (c.get("wsb_hype") or {}).get("hype_label", "none")
                if wsb_label == "extreme":
                    notional = round(notional * 0.5, 2)
                    print(f"[agent] {c['symbol']} WSB extreme hype — position halved to ${notional:.0f}")

                _veto = bool(c.get("veto"))
                _base_reason = c.get("reason", "Top S&P 500 scanner pick")
                _buy_reason = (f"🚫 AI 排雷(需人工确认): {c.get('veto_reason','')} | {_base_reason}"
                               if _veto else _base_reason)
                trade = _make_trade(
                    symbol=c["symbol"], side="buy",
                    notional=notional, qty=None,
                    signal=c.get("signal", "HOLD"),
                    confidence=0.8,   # v8 机械买入:无分数概念,置信固定;自动/人工只看 veto
                    reason=_buy_reason,
                    source="scanner",
                    stop_loss=stop,
                    target_price=c.get("target_price"),
                    price=price,
                    rsi=c.get("rsi"),
                    momentum_5d=c.get("momentum_5d"),
                    volume_ratio=c.get("volume_ratio"),
                    near_breakout=c.get("near_breakout"),
                    universe=c.get("universe"),
                    screen_track="momentum",
                    veto=_veto,
                    veto_reason=c.get("veto_reason") if _veto else None,
                )
                if _veto:
                    print(f"[agent] {c['symbol']} AI 排雷 → 人工审核队列: {c.get('veto_reason','')}")
                if _add_trade(trade, owned_symbols):
                    summary["signals_found"] += 1
                    summary["trades_queued"] += 1
                    scanner_added += 1
                    _new_trade_ids.add(trade["id"])
            if scanner_added:
                summary["sources"].append("scanner")

        # ── v8: 自选不搞特殊 ──────────────────────────────────────────────────
        # 自选股(watchlist)作为 force_symbols 已并入 scanner 候选,走同一道趋势门 +
        # 动量排名 + 上面的机械买入路径。v7 那条独立的「AI信号门+严格入场+Gate A/B」
        # 自选路径已删除 —— 它正是新老策略混杂、买入与排名不一致的根源。

        # ── 0. Over-allocation rebalance ─────────────────────────────────────
        # If total invested > 95% of equity (margin territory), sell weakest positions
        # until allocation drops back to 90%. Runs BEFORE normal sell logic.
        OVERALLOC_THRESHOLD = 0.95
        OVERALLOC_TARGET    = 0.90
        _overalloc_symbols: set[str] = set()
        if alpaca_positions and equity > 0:
            invested    = sum(float(p.market_value) for p in alpaca_positions)
            alloc_pct   = invested / equity
            if alloc_pct > OVERALLOC_THRESHOLD:
                need_to_sell = invested - equity * OVERALLOC_TARGET
                print(f"[agent] ⚠️  Over-allocated {alloc_pct:.1%} → selling ${need_to_sell:,.0f} "
                      f"to reach {OVERALLOC_TARGET:.0%} target")
                # Sell worst P&L first; break ties by largest position size
                sell_cands = sorted(
                    alpaca_positions,
                    key=lambda p: (float(p.unrealized_plpc), -float(p.market_value))
                )
                sold_so_far = 0.0
                for sp in sell_cands:
                    if sold_so_far >= need_to_sell:
                        break
                    if sp.symbol in alpaca_open_sell_symbols:
                        continue
                    mv = float(sp.market_value)
                    plpc = float(sp.unrealized_plpc) * 100
                    print(f"[agent] Over-alloc sell: {sp.symbol} mv=${mv:,.0f} P&L={plpc:+.1f}%")
                    trade = _make_trade(
                        symbol=sp.symbol, side="sell",
                        notional=None, qty=None,
                        signal="SELL", confidence=0.95,
                        reason=f"Over-allocation rebalance: {alloc_pct:.1%} invested, "
                               f"reducing to {OVERALLOC_TARGET:.0%} of equity",
                        source="overalloc",
                        price=float(sp.current_price),
                    )
                    if _add_trade(trade, owned_symbols):
                        summary["signals_found"] += 1
                        summary["trades_queued"] += 1
                        sold_so_far += mv
                        _overalloc_symbols.add(sp.symbol)
                        _new_trade_ids.add(trade["id"])
                if _overalloc_symbols:
                    summary.setdefault("sources", []).append("overalloc")

        # ── 3. Holdings: SELL / REDUCE ────────────────────────────────────────
        # First pass: hard stop-loss + trailing stop from live Alpaca positions
        HARD_STOP_PCT  = -stop_loss_pct * 100   # fallback for untracked positions
        TRAIL_TRIGGER = 0.06   # v8: 浮盈 +6% 激活追踪(原 +10%)— 顺势早保护
        TRAIL_PCT     = 0.08   # v8: 高水位回撤 8% 才走(原 5%)— 给赢家呼吸空间、让它跑

        # Build symbol → trade entry from most-recent buy in trades.json
        all_trades = _load_from_disk()
        _sym_trade: dict[str, dict] = {}
        _sym_trade_id: dict[str, str] = {}
        for tid, _t in all_trades.items():
            if _t.get("side") == "buy" and _t.get("symbol"):
                sym_ = _t["symbol"]
                if sym_ not in _sym_trade or _t.get("created_at", "") > _sym_trade[sym_].get("created_at", ""):
                    _sym_trade[sym_] = _t
                    _sym_trade_id[sym_] = tid
        _trades_dirty = False   # track if we need to save

        holdings_added = 0
        hard_stop_symbols: set[str] = set()
        for ap in alpaca_positions:
            if ap.symbol in alpaca_open_sell_symbols:
                print(f"[agent] skip {ap.symbol} hard stop — open sell order already in Alpaca")
                continue
            if ap.symbol in _overalloc_symbols:
                continue
            plpc = float(ap.unrealized_plpc) * 100
            current_px = float(ap.current_price)
            entry_trade = _sym_trade.get(ap.symbol, {})
            per_pos_stop = entry_trade.get("stop_loss")
            entry_px = entry_trade.get("price") or (current_px / (1 + plpc / 100) if plpc != -100 else current_px)

            # ── Update high water mark ────────────────────────────────────────
            high_water = float(entry_trade.get("high_water_price") or entry_px)
            if current_px > high_water:
                high_water = current_px
                if ap.symbol in _sym_trade_id:
                    all_trades[_sym_trade_id[ap.symbol]]["high_water_price"] = round(high_water, 4)
                    _trades_dirty = True

            # ── Activate trailing stop once target is reached (v8: 浮盈 +6% 激活) ──
            trail_active = bool(entry_trade.get("trail_active", False))
            if not trail_active and entry_px and current_px >= entry_px * (1 + TRAIL_TRIGGER):
                trail_active = True
                if ap.symbol in _sym_trade_id:
                    all_trades[_sym_trade_id[ap.symbol]]["trail_active"] = True
                    _trades_dirty = True
                print(f"[agent] Trailing stop ACTIVATED: {ap.symbol} @ ${current_px:.2f} (+{plpc:.1f}%) high=${high_water:.2f}")

            # ── Check exit conditions ─────────────────────────────────────────
            trail_stop_px = high_water * (1 - TRAIL_PCT) if trail_active else None
            hard_stop_hit  = (current_px <= float(per_pos_stop)) if per_pos_stop else (plpc <= HARD_STOP_PCT)
            trail_stop_hit = trail_active and trail_stop_px and current_px <= trail_stop_px

            if trail_stop_hit:
                reason_txt = f"Trailing stop: {ap.symbol} dropped ${current_px:.2f} from high ${high_water:.2f} ({((current_px/high_water)-1)*100:.1f}%)"
                print(f"[agent] {reason_txt}")
                trade = _make_trade(
                    symbol=ap.symbol, side="sell",
                    notional=None, qty=None,
                    signal="SELL", confidence=0.85,
                    reason=reason_txt, source="trail_stop",
                    price=current_px,
                )
                if _add_trade(trade, owned_symbols):
                    summary["signals_found"] += 1
                    summary["trades_queued"] += 1
                    holdings_added += 1
                    hard_stop_symbols.add(ap.symbol)
                    _new_trade_ids.add(trade["id"])
            elif hard_stop_hit:
                stop_desc = f"stop price ${per_pos_stop:.2f} (structured)" if per_pos_stop else f"threshold {HARD_STOP_PCT:.1f}%"
                print(f"[agent] Hard stop: {ap.symbol} @ ${current_px:.2f} hit {stop_desc} (P&L {plpc:.1f}%)")
                trade = _make_trade(
                    symbol=ap.symbol, side="sell",
                    notional=None, qty=None,
                    signal="SELL", confidence=0.9,
                    reason=f"Hard stop: {ap.symbol} @ ${current_px:.2f} hit {stop_desc} (P&L {plpc:.1f}%)",
                    source="hard_stop",
                    price=current_px,
                )
                if _add_trade(trade, owned_symbols):
                    summary["signals_found"] += 1
                    summary["trades_queued"] += 1
                    holdings_added += 1
                    hard_stop_symbols.add(ap.symbol)
                    _new_trade_ids.add(trade["id"])

        if _trades_dirty:
            _save_to_disk(all_trades)

        # Second pass: 机械卖出信号(holdings_monitor 只发 SELL/HOLD,v8 无 REDUCE/软清仓)
        for pos in holdings_cache.get("positions", []):
            if pos.get("sell_signal") != "SELL":
                continue
            if pos["symbol"] not in owned_symbols:
                print(f"[agent] skip {pos['symbol']} sell — position already closed")
                continue
            if pos["symbol"] in hard_stop_symbols:
                continue   # already queued via hard stop
            if pos["symbol"] in _overalloc_symbols:
                continue   # already queued by over-allocation rebalance
            if pos["symbol"] in alpaca_open_sell_symbols:
                print(f"[agent] skip {pos['symbol']} SELL — open sell order already in Alpaca")
                continue

            trade = _make_trade(
                symbol=pos["symbol"], side="sell",
                notional=None,
                qty=None,   # 全部平仓
                signal="SELL",
                confidence=0.8,
                reason=pos.get("reason", "Holdings monitor sell signal"),
                source="holdings",
                price=pos.get("current_price"),
            )
            if _add_trade(trade, owned_symbols):
                summary["signals_found"] += 1
                summary["trades_queued"] += 1
                holdings_added += 1
                _new_trade_ids.add(trade["id"])

        if holdings_added:
            summary["sources"].append("holdings")

        # ── Auto-approve: execute high-confidence trades from THIS run only ──
        # Only auto-approves trades queued in this run — not stale ones from prior runs.
        # Track running cash spend so we never exceed spendable_cash across batch approvals.
        if _is_auto_approve_enabled():
            auto_approved = 0
            committed_this_run = 0.0   # cumulative notional approved in this run
            for trade in list(_pending.values()):
                if trade["id"] not in _new_trade_ids:
                    continue   # only act on trades queued this run
                if trade["status"] != "pending":
                    continue

                # v8 审批:纯开关,无分数概念(已删 AI-score 时代的阈值逻辑)。
                #   买入 → 默认自动;只有 AI 排雷(veto=True)才留给人工审核。
                #   卖出 → 机械保护退出(止损/追踪/MA20),始终自动执行,不挡。
                if trade["side"] == "buy" and trade.get("veto"):
                    print(f"[agent] {trade['symbol']} 留待人工审核 — AI 排雷: {trade.get('veto_reason','')}")
                    continue   # 留 pending,等人工批准/拒绝

                # Guard: ensure we still have enough spendable cash after prior approvals
                if trade["side"] == "buy":
                    notional = trade.get("notional") or 0
                    if committed_this_run + notional > spendable_cash:
                        print(f"[agent] Auto-approve SKIP {trade['symbol']} — "
                              f"would exceed spendable cash "
                              f"(committed=${committed_this_run:.0f} + ${notional:.0f} > ${spendable_cash:.0f})")
                        continue
                try:
                    approve_trade(trade["id"])
                    if trade["side"] == "buy":
                        committed_this_run += trade.get("notional") or 0
                    auto_approved += 1
                    print(f"[agent] Auto-approved {trade['side'].upper()} {trade['symbol']} "
                          f"(src={trade.get('source')}, committed=${committed_this_run:.0f}/{spendable_cash:.0f})")
                except Exception as e:
                    print(f"[agent] Auto-approve failed for {trade['id']}: {e}")
            if auto_approved:
                summary["auto_approved"] = auto_approved

    except Exception as e:
        summary["status"] = "error"
        summary["error"] = str(e)
        import traceback as _tb
        print(f"[agent] run error: {e}\n{_tb.format_exc()}")
    finally:
        _agent_running = False

    _run_log.append(summary)
    if len(_run_log) > MAX_LOG:
        _run_log.pop(0)
    _save_log(_run_log)

    return summary


# ── Auto-approve config ───────────────────────────────────────────────────────

_AUTO_APPROVE_FILE = Path(__file__).parent.parent.parent / "data" / "auto_approve.json"

# 保护性卖出比买入更易自动放行（减风险要果断、买入要谨慎）：
def _is_auto_approve_enabled() -> bool:
    """v8 自动执行 = 纯开关,无分数概念。默认开。"""
    try:
        if _AUTO_APPROVE_FILE.exists():
            cfg = json.loads(_AUTO_APPROVE_FILE.read_text())
            return bool(cfg.get("enabled", True))
    except Exception:
        pass
    return True   # default: autonomous


def set_auto_approve(enabled: bool) -> dict:
    """开/关自动执行,持久化到磁盘(纯布尔,无阈值)。"""
    cfg = {"enabled": bool(enabled)}
    _AUTO_APPROVE_FILE.parent.mkdir(exist_ok=True)
    _AUTO_APPROVE_FILE.write_text(json.dumps(cfg))
    print(f"[agent] Autonomous execution {'ENABLED' if enabled else 'DISABLED'}")
    return cfg


def get_auto_approve_config() -> dict:
    """返回当前自动执行配置(纯开关)。"""
    try:
        if _AUTO_APPROVE_FILE.exists():
            cfg = json.loads(_AUTO_APPROVE_FILE.read_text())
            return {"enabled": bool(cfg.get("enabled", True))}
    except Exception:
        pass
    return {"enabled": True}   # default: autonomous
