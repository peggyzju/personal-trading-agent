"""
Strategy Version Control
========================
Tracks every change to strategy parameters as a named version.
Each closed trade is tagged with the version active at entry time.
Computes per-version stats with Wilson confidence intervals.

Data files:
  data/strategy_versions.json  — version history
  data/trade_history.json      — closed trades with version + regime tags
"""
from __future__ import annotations

import json
import math
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

_ROOT        = Path(__file__).parents[2] / "data"
_VER_PATH    = _ROOT / "strategy_versions.json"
_HIST_PATH   = _ROOT / "trade_history.json"
_OVR_PATH    = _ROOT / "strategy_overrides.json"
_TRADES_PATH = _ROOT / "trades.json"


def _lookup_screen_track(alpaca_order_id: Optional[str], symbol: str) -> Optional[str]:
    """Find the entry track ("momentum"/"compression"/"watchlist") for a closed
    trade by matching the original buy order in trades.json.

    Primary match: executed_order_id == alpaca_order_id (exact).
    Fallback: most recent buy trade for the same symbol that carries a track.
    Returns None if no track was recorded (e.g. pre-instrumentation trades).
    """
    try:
        trades = json.loads(_TRADES_PATH.read_text())
    except Exception:
        return None
    rows = trades.values() if isinstance(trades, dict) else trades
    buys = [t for t in rows if isinstance(t, dict) and t.get("side") == "buy"]
    if alpaca_order_id:
        for t in buys:
            if t.get("executed_order_id") == alpaca_order_id and t.get("screen_track"):
                return t["screen_track"]
    # Fallback: latest buy for this symbol with a track label
    sym_buys = sorted(
        (t for t in buys if t.get("symbol") == symbol and t.get("screen_track")),
        key=lambda t: t.get("created_at", ""), reverse=True,
    )
    return sym_buys[0]["screen_track"] if sym_buys else None


# ─────────────────────────────────────────────────────────────────────────────
# Version store
# ─────────────────────────────────────────────────────────────────────────────

def _load_versions() -> list[dict]:
    try:
        return json.loads(_VER_PATH.read_text())
    except Exception:
        return []


def _save_versions(versions: list[dict]) -> None:
    _VER_PATH.write_text(json.dumps(versions, indent=2, default=str))


def create_version(
    stop_loss_pct: float,
    max_position_pct: float,
    entry_rsi_max: Optional[float] = None,
    entry_vma20_max: Optional[float] = None,
    notes: str = "",
) -> dict:
    """
    Record a new strategy version. Called whenever overrides change.
    Returns the new version dict.
    """
    versions = _load_versions()

    # Auto-increment version number
    if versions:
        last_major = int(versions[-1]["version"].split(".")[0].lstrip("v"))
        ver_id = f"v{last_major + 1}.0"
    else:
        ver_id = "v1.0"

    version = {
        "version":          ver_id,
        "created_at":       datetime.now(timezone.utc).isoformat(),
        "params": {
            "stop_loss_pct":      stop_loss_pct,
            "max_position_pct":   max_position_pct,
            "entry_rsi_max":      entry_rsi_max,
            "entry_vma20_max":    entry_vma20_max,
        },
        "notes": notes,
    }
    versions.append(version)
    _save_versions(versions)
    print(f"[versions] Created {ver_id}: stop={stop_loss_pct}% | "
          f"rsi_max={entry_rsi_max} | vma20_max={entry_vma20_max}%")
    return version


def get_current_version() -> Optional[dict]:
    """Return the most recent strategy version."""
    versions = _load_versions()
    return versions[-1] if versions else None


def get_version_at(timestamp: str) -> Optional[dict]:
    """Return the version that was active at a given ISO timestamp."""
    versions = _load_versions()
    active = None
    for v in versions:
        if v["created_at"] <= timestamp:
            active = v
    return active


def get_all_versions() -> list[dict]:
    return _load_versions()


# ─────────────────────────────────────────────────────────────────────────────
# Trade history store
# ─────────────────────────────────────────────────────────────────────────────

def _load_history() -> list[dict]:
    try:
        return json.loads(_HIST_PATH.read_text())
    except Exception:
        return []


def _save_history(trades: list[dict]) -> None:
    _HIST_PATH.write_text(json.dumps(trades, indent=2, default=str))


def record_closed_trade(
    symbol: str,
    entry_date: str,
    exit_date: str,
    entry_price: float,
    exit_price: float,
    pnl_pct: float,
    exit_reason: str,
    days_held: int,
    notional: float,
    rsi_at_entry: Optional[float] = None,
    vma20_at_entry: Optional[float] = None,
    market_regime: Optional[str] = None,
    alpaca_order_id: Optional[str] = None,
    screen_track: Optional[str] = None,
) -> dict:
    """Append one closed trade to trade_history.json with version tag."""
    history = _load_history()

    # Avoid duplicates (same symbol + entry_date)
    key = f"{symbol}_{entry_date}"
    if any(f"{t['symbol']}_{t['entry_date']}" == key for t in history):
        return {}

    version = get_version_at(entry_date + "T00:00:00+00:00")
    ver_id  = version["version"] if version else "unknown"

    # Backfill entry track from the original buy order if caller didn't pass it
    if screen_track is None:
        screen_track = _lookup_screen_track(alpaca_order_id, symbol)

    trade = {
        "symbol":          symbol,
        "entry_date":      entry_date,
        "exit_date":       exit_date,
        "entry_price":     round(entry_price, 2),
        "exit_price":      round(exit_price, 2),
        "pnl_pct":         round(pnl_pct, 2),
        "exit_reason":     exit_reason,
        "days_held":       days_held,
        "notional":        round(notional, 0),
        "dollar_pnl":      round(notional * pnl_pct / 100, 0),
        "rsi_at_entry":    rsi_at_entry,
        "vma20_at_entry":  vma20_at_entry,
        "market_regime":   market_regime,
        "screen_track":    screen_track,
        "strategy_version": ver_id,
        "alpaca_order_id": alpaca_order_id,
        "recorded_at":     datetime.now(timezone.utc).isoformat(),
    }
    history.append(trade)
    _save_history(history)
    return trade


def get_trade_history(version: Optional[str] = None, days: int = 90) -> list[dict]:
    """Return closed trades, optionally filtered by version."""
    history = _load_history()
    if version:
        history = [t for t in history if t.get("strategy_version") == version]
    return history


# ─────────────────────────────────────────────────────────────────────────────
# Statistics with confidence intervals
# ─────────────────────────────────────────────────────────────────────────────

def _wilson_ci(successes: int, n: int, z: float = 1.645) -> tuple[float, float]:
    """Wilson score interval for a proportion. Default z=1.645 → 90% CI."""
    if n == 0:
        return (0.0, 1.0)
    p = successes / n
    center = (p + z**2 / (2*n)) / (1 + z**2 / n)
    margin = z * math.sqrt(p*(1-p)/n + z**2/(4*n**2)) / (1 + z**2/n)
    return (max(0, round(center - margin, 3)), min(1, round(center + margin, 3)))


def _min_trades_needed(target_win_rate: float = 0.50, z: float = 1.645,
                       margin: float = 0.10) -> int:
    """
    Estimate trades needed so the CI half-width <= margin.
    Uses conservative p=0.5 for maximum variance.
    """
    return math.ceil((z / margin) ** 2 * 0.25)


def compute_version_stats(trades: list[dict]) -> dict:
    """Full stats for a list of trades, with confidence intervals."""
    if not trades:
        return {"n": 0, "status": "no_trades"}

    pnls    = [t["pnl_pct"] for t in trades]
    winners = [p for p in pnls if p > 0]
    losers  = [p for p in pnls if p <= 0]
    n       = len(pnls)

    win_rate    = len(winners) / n
    win_ci      = _wilson_ci(len(winners), n)
    pf          = abs(sum(winners) / sum(losers)) if sum(losers) != 0 else None
    avg_win     = sum(winners) / len(winners) if winners else 0
    avg_loss    = sum(losers)  / len(losers)  if losers  else 0
    total_ret   = sum(t.get("dollar_pnl", 0) for t in trades)

    needed = _min_trades_needed()
    is_significant = n >= needed

    # Exit breakdown
    exits: dict[str, int] = {}
    for t in trades:
        r = t.get("exit_reason", "unknown")
        exits[r] = exits.get(r, 0) + 1

    # Entry quality
    rsi_vals  = [t["rsi_at_entry"]   for t in trades if t.get("rsi_at_entry")   is not None]
    vma20_vals = [t["vma20_at_entry"] for t in trades if t.get("vma20_at_entry") is not None]

    # Regime breakdown
    regimes: dict[str, dict] = {}
    for t in trades:
        reg = t.get("market_regime") or "unknown"
        if reg not in regimes:
            regimes[reg] = {"n": 0, "wins": 0, "pnls": []}
        regimes[reg]["n"]    += 1
        regimes[reg]["pnls"].append(t["pnl_pct"])
        if t["pnl_pct"] > 0:
            regimes[reg]["wins"] += 1
    for reg, d in regimes.items():
        d["win_rate"] = round(d["wins"] / d["n"] * 100, 1) if d["n"] else 0
        d["avg_pnl"]  = round(sum(d["pnls"]) / len(d["pnls"]), 2) if d["pnls"] else 0
        del d["pnls"]

    # Track breakdown (momentum=Track1 / compression=Track2 / watchlist)
    tracks: dict[str, dict] = {}
    for t in trades:
        tk = t.get("screen_track") or "unknown"
        if tk not in tracks:
            tracks[tk] = {"n": 0, "wins": 0, "pnls": [], "wins_pnl": [], "loss_pnl": []}
        tracks[tk]["n"] += 1
        tracks[tk]["pnls"].append(t["pnl_pct"])
        if t["pnl_pct"] > 0:
            tracks[tk]["wins"] += 1
            tracks[tk]["wins_pnl"].append(t["pnl_pct"])
        else:
            tracks[tk]["loss_pnl"].append(t["pnl_pct"])
    for tk, d in tracks.items():
        d["win_rate"]      = round(d["wins"] / d["n"] * 100, 1) if d["n"] else 0
        d["avg_pnl"]       = round(sum(d["pnls"]) / len(d["pnls"]), 2) if d["pnls"] else 0
        win_sum, loss_sum  = sum(d["wins_pnl"]), sum(d["loss_pnl"])
        d["profit_factor"] = round(abs(win_sum / loss_sum), 2) if loss_sum != 0 else None
        del d["pnls"], d["wins_pnl"], d["loss_pnl"]

    return {
        "n":                    n,
        "needed_for_significance": needed,
        "is_significant":       is_significant,
        "progress_pct":         round(min(n / needed * 100, 100), 0),
        "win_rate":             round(win_rate * 100, 1),
        "win_rate_ci_90":       [round(win_ci[0]*100,1), round(win_ci[1]*100,1)],
        "avg_win_pct":          round(avg_win, 2),
        "avg_loss_pct":         round(avg_loss, 2),
        "profit_factor":        round(pf, 2) if pf else None,
        "total_dollar_pnl":     round(total_ret, 0),
        "exit_breakdown":       exits,
        "entry_quality": {
            "rsi_mean":    round(sum(rsi_vals)/len(rsi_vals), 1)   if rsi_vals   else None,
            "vma20_mean":  round(sum(vma20_vals)/len(vma20_vals),1) if vma20_vals else None,
        },
        "by_regime":            regimes,
        "by_track":             tracks,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Comparison report
# ─────────────────────────────────────────────────────────────────────────────

def _verdict(v1: dict, v2: dict) -> str:
    """Plain-language verdict comparing two version stat dicts."""
    n2     = v2.get("n", 0)
    needed = v2.get("needed_for_significance", 68)
    if n2 < 10:
        return f"⏳ 证据不足：当前版本仅 {n2} 笔平仓，需要 {needed} 笔才有统计意义"

    signals = []
    v1_wr = v1.get("win_rate", 0)
    v2_wr = v2.get("win_rate", 0)
    v2_ci = v2.get("win_rate_ci_90", [0, 100])

    # Win rate
    if v2_ci[0] > v1_wr:
        signals.append("✅ 胜率显著提升")
    elif v2_wr > v1_wr:
        signals.append("↑ 胜率提升（尚不显著）")
    else:
        signals.append("↓ 胜率下降")

    # Profit factor
    if v2.get("profit_factor") and v1.get("profit_factor"):
        if v2["profit_factor"] > v1["profit_factor"]:
            signals.append("✅ 盈亏比改善")
        else:
            signals.append("↓ 盈亏比下降")

    green = sum(1 for s in signals if s.startswith("✅"))
    if green == len(signals):
        return "🟢 新策略全面优于旧策略 — " + " | ".join(signals)
    elif green > 0:
        return "🟡 新策略部分改善 — " + " | ".join(signals)
    else:
        return "🔴 新策略未见改善 — " + " | ".join(signals)


def compare_versions(v1_id: str, v2_id: str) -> dict:
    """Side-by-side comparison of two strategy versions."""
    history  = _load_history()
    versions = {v["version"]: v for v in _load_versions()}

    v1_trades = [t for t in history if t.get("strategy_version") == v1_id]
    v2_trades = [t for t in history if t.get("strategy_version") == v2_id]

    v1_stats = compute_version_stats(v1_trades)
    v2_stats = compute_version_stats(v2_trades)

    return {
        "v1": {**versions.get(v1_id, {}), "stats": v1_stats},
        "v2": {**versions.get(v2_id, {}), "stats": v2_stats},
        "verdict": _verdict(v1_stats, v2_stats),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Sync closed trades from Alpaca (best-effort, idempotent)
# ─────────────────────────────────────────────────────────────────────────────

def sync_closed_trades_from_alpaca(alpaca_api, days: int = 30) -> int:
    """
    Pull recent filled orders from Alpaca, match buy→sell pairs,
    and record them in trade_history.json.
    Returns count of new trades added.
    """
    from datetime import timedelta
    import alpaca_trade_api as tradeapi

    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    orders = alpaca_api.list_orders(
        status="closed", after=since, limit=500, direction="asc"
    )

    # Group by symbol: buys first, then match sells
    buys:  dict[str, list] = {}
    sells: dict[str, list] = {}
    for o in orders:
        if o.status != "filled" or not o.filled_avg_price:
            continue
        sym = o.symbol
        if o.side == "buy":
            buys.setdefault(sym, []).append(o)
        else:
            sells.setdefault(sym, []).append(o)

    # Load market regime cache for tagging
    try:
        regime_cache = json.loads(
            (Path(__file__).parents[2] / "data" / "regime_cache.json").read_text()
        )
        current_regime = regime_cache.get("regime", "NEUTRAL")
    except Exception:
        current_regime = "NEUTRAL"

    added = 0
    for sym, sell_orders in sells.items():
        buy_orders = buys.get(sym, [])
        if not buy_orders:
            continue

        for sell in sell_orders:
            # Match to closest preceding buy
            sell_time = sell.filled_at
            if isinstance(sell_time, str):
                sell_time = datetime.fromisoformat(sell_time.replace("Z", "+00:00"))

            matching_buys = [
                b for b in buy_orders
                if (datetime.fromisoformat(b.filled_at.replace("Z", "+00:00"))
                    if isinstance(b.filled_at, str)
                    else b.filled_at) < sell_time
            ]
            if not matching_buys:
                continue

            buy = matching_buys[-1]  # most recent buy before this sell
            buy_time = (datetime.fromisoformat(buy.filled_at.replace("Z", "+00:00"))
                        if isinstance(buy.filled_at, str) else buy.filled_at)
            sell_time_dt = (datetime.fromisoformat(sell.filled_at.replace("Z", "+00:00"))
                            if isinstance(sell.filled_at, str) else sell.filled_at)

            entry_price = float(buy.filled_avg_price)
            exit_price  = float(sell.filled_avg_price)
            pnl_pct     = (exit_price - entry_price) / entry_price * 100
            days_held   = (sell_time_dt - buy_time).days
            notional    = entry_price * float(buy.filled_qty or 0)

            entry_date  = buy_time.strftime("%Y-%m-%d")
            exit_date   = sell_time_dt.strftime("%Y-%m-%d")

            result = record_closed_trade(
                symbol=sym,
                entry_date=entry_date,
                exit_date=exit_date,
                entry_price=entry_price,
                exit_price=exit_price,
                pnl_pct=round(pnl_pct, 2),
                exit_reason="alpaca_sync",
                days_held=days_held,
                notional=notional,
                market_regime=current_regime,
                alpaca_order_id=buy.id,
            )
            if result:
                added += 1

    print(f"[versions] Synced {added} new closed trades from Alpaca")
    return added
