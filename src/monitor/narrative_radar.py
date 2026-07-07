"""Market Narrative Radar.

Detects broad market narrative shocks from price-confirmed cross-asset / sector
rotation. This is an explanatory context layer for Maya and the dashboard; it
does not place orders, sell holdings, or override SPY regime.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import json
from pathlib import Path

from src.monitor.sp500_scanner import AI_HARDWARE_SYMBOLS, AI_SOFTWARE_SYMBOLS

_RADAR_FILE = Path(__file__).parent.parent.parent / "data" / "narrative_radar.json"
_CACHE_TTL_SECONDS = 900


THEME_CATALOG: dict[str, dict] = {
    "AI_COMPUTE_OVERSUPPLY": {
        "headline": "AI Compute Oversupply",
        "summary": "硬件链被重新定价,软件/应用相对占优。",
        "affected_groups": ["AI_HARDWARE", "MEMORY", "SERVER"],
        "beneficiary_groups": ["SOFTWARE", "AI_APPS"],
        "action_hint": "soft_overlay_enabled",
    },
    "RATE_SHOCK": {
        "headline": "Rate Shock",
        "summary": "长端利率上行压制高估值成长股。",
        "affected_groups": ["HIGH_MULTIPLE_GROWTH", "SMALL_CAP"],
        "beneficiary_groups": ["CASH_FLOW_DEFENSIVE"],
        "action_hint": "watch_growth_duration_risk",
    },
    "ENERGY_SHOCK": {
        "headline": "Energy Shock",
        "summary": "能源价格/能源股显著跑赢,通胀敏感链条承压。",
        "affected_groups": ["AIRLINES", "CONSUMER_DISCRETIONARY", "TRANSPORT"],
        "beneficiary_groups": ["ENERGY"],
        "action_hint": "watch_inflation_sensitive_groups",
    },
    "CREDIT_LIQUIDITY_SHOCK": {
        "headline": "Credit / Liquidity Shock",
        "summary": "信用或小盘流动性变弱,高 beta 风险偏好下降。",
        "affected_groups": ["SMALL_CAP", "HIGH_BETA", "UNPROFITABLE_GROWTH"],
        "beneficiary_groups": ["MEGA_CAP_QUALITY"],
        "action_hint": "reduce_new_high_beta_enthusiasm",
    },
}


def _empty_item(theme: str, reason: str = "not price-confirmed") -> dict:
    meta = THEME_CATALOG[theme]
    return {
        "theme": theme,
        "status": "inactive",
        "severity": "none",
        "headline": meta["headline"],
        "summary": reason,
        "affected_groups": meta["affected_groups"],
        "beneficiary_groups": meta["beneficiary_groups"],
        "price_confirmed": False,
        "confidence": 0.0,
        "metrics": {},
        "action_hint": "none",
        "reason": reason,
    }


def _load_cache() -> dict | None:
    try:
        if not _RADAR_FILE.exists():
            return None
        data = json.loads(_RADAR_FILE.read_text())
        updated_at = data.get("updated_at")
        if not updated_at:
            return None
        ts = datetime.fromisoformat(updated_at.replace("Z", "+00:00")).timestamp()
        age = datetime.now(timezone.utc).timestamp() - ts
        if age <= _CACHE_TTL_SECONDS:
            return data
    except Exception:
        return None
    return None


def _save_cache(data: dict) -> None:
    try:
        _RADAR_FILE.parent.mkdir(exist_ok=True)
        _RADAR_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    except Exception:
        pass


def _fetch_closes(symbols: list[str], days: int = 45) -> pd.DataFrame:
    from src.trader.alpaca_trader import get_client

    start = (datetime.now(timezone.utc) - timedelta(days=days)).date().isoformat()
    df = get_client().get_bars(sorted(set(symbols)), "1Day", start=start, feed="iex").df
    if df is None or df.empty:
        return pd.DataFrame()

    closes: dict[str, pd.Series] = {}
    if "symbol" in df.columns:
        for sym, sub in df.groupby("symbol"):
            closes[sym] = sub["close"]
    elif isinstance(df.index, pd.MultiIndex):
        for sym in df.index.get_level_values(0).unique():
            closes[sym] = df.loc[sym]["close"]
    elif len(symbols) == 1:
        closes[symbols[0]] = df["close"]

    out = pd.DataFrame(closes).sort_index()
    if not out.empty:
        out.index = pd.to_datetime(out.index).tz_localize(None)
    return out


def _median_return(close: pd.DataFrame, symbols: list[str], days: int) -> float | None:
    cols = [s for s in symbols if s in close.columns]
    if not cols or len(close) <= days:
        return None
    rets = close[cols].iloc[-1] / close[cols].shift(days).iloc[-1] - 1
    vals = rets.replace([np.inf, -np.inf], np.nan).dropna()
    if vals.empty:
        return None
    return float(vals.median() * 100)


def _return(close: pd.DataFrame, symbol: str, days: int) -> float | None:
    if symbol not in close.columns or len(close) <= days:
        return None
    prev = float(close[symbol].shift(days).iloc[-1])
    cur = float(close[symbol].iloc[-1])
    if not prev:
        return None
    return (cur - prev) / prev * 100


def _breadth_above_ma20(close: pd.DataFrame, symbols: list[str]) -> float | None:
    cols = [s for s in symbols if s in close.columns]
    if not cols or len(close) < 20:
        return None
    ma20 = close[cols].rolling(20).mean()
    vals = (close[cols].iloc[-1] > ma20.iloc[-1]).dropna()
    if vals.empty:
        return None
    return float(vals.mean())


def _status_from_conditions(conditions: list[bool]) -> str:
    passed = sum(1 for c in conditions if c)
    if passed == len(conditions):
        return "active"
    if passed >= max(1, len(conditions) - 1):
        return "watch"
    return "inactive"


def _severity(status: str, score: float) -> str:
    if status != "active":
        return "none" if status == "inactive" else "low"
    if score >= 2.5:
        return "high"
    if score >= 1.5:
        return "medium"
    return "low"


def _ai_compute_oversupply(close: pd.DataFrame) -> dict:
    theme = "AI_COMPUTE_OVERSUPPLY"
    hw = sorted(AI_HARDWARE_SYMBOLS)
    sw = sorted(AI_SOFTWARE_SYMBOLS)
    hw_1d = _median_return(close, hw, 1)
    sw_3d = _median_return(close, sw, 3)
    hw_3d = _median_return(close, hw, 3)
    sw_breadth = _breadth_above_ma20(close, sw)
    hw_breadth = _breadth_above_ma20(close, hw)
    if None in (hw_1d, sw_3d, hw_3d, sw_breadth, hw_breadth):
        return _empty_item(theme, "insufficient price data")

    sw_minus_hw_3d = sw_3d - hw_3d
    conditions = [
        sw_minus_hw_3d > 3.0,
        hw_1d < -2.0,
        sw_breadth > hw_breadth,
    ]
    status = _status_from_conditions(conditions)
    score = (max(sw_minus_hw_3d, 0) / 4.0) + (max(-hw_1d, 0) / 3.0) + max(sw_breadth - hw_breadth, 0)
    meta = THEME_CATALOG[theme]
    return {
        "theme": theme,
        "status": status,
        "severity": _severity(status, score),
        "headline": meta["headline"],
        "summary": meta["summary"] if status != "inactive" else "AI 硬件相对软件尚未形成完整价格确认。",
        "affected_groups": meta["affected_groups"],
        "beneficiary_groups": meta["beneficiary_groups"],
        "price_confirmed": status == "active",
        "confidence": round(min(0.95, 0.35 + score / 5), 2) if status != "inactive" else 0.0,
        "metrics": {
            "hardware_1d_median_pct": round(hw_1d, 2),
            "software_minus_hardware_3d_pct": round(sw_minus_hw_3d, 2),
            "software_breadth": round(sw_breadth, 3),
            "hardware_breadth": round(hw_breadth, 3),
        },
        "action_hint": meta["action_hint"] if status == "active" else "watch_only",
        "reason": (
            f"软件-硬件3日差 {sw_minus_hw_3d:+.1f}%, "
            f"硬件1日 {hw_1d:+.1f}%, breadth 软件 {sw_breadth:.0%}/硬件 {hw_breadth:.0%}"
        ),
    }


def _rate_shock(close: pd.DataFrame) -> dict:
    theme = "RATE_SHOCK"
    tlt_1d = _return(close, "TLT", 1)
    qqq_1d = _return(close, "QQQ", 1)
    spy_1d = _return(close, "SPY", 1)
    if None in (tlt_1d, qqq_1d, spy_1d):
        return _empty_item(theme, "insufficient price data")
    growth_rel = qqq_1d - spy_1d
    conditions = [tlt_1d < -1.2, growth_rel < -0.6]
    status = _status_from_conditions(conditions)
    score = max(-tlt_1d, 0) + max(-growth_rel, 0)
    meta = THEME_CATALOG[theme]
    return {
        **_empty_item(theme),
        "status": status,
        "severity": _severity(status, score),
        "summary": meta["summary"] if status != "inactive" else "利率冲击未被价格确认。",
        "price_confirmed": status == "active",
        "confidence": round(min(0.9, 0.3 + score / 5), 2) if status != "inactive" else 0.0,
        "metrics": {"tlt_1d_pct": round(tlt_1d, 2), "qqq_vs_spy_1d_pct": round(growth_rel, 2)},
        "action_hint": meta["action_hint"] if status == "active" else "watch_only",
        "reason": f"TLT {tlt_1d:+.1f}%, QQQ-SPY {growth_rel:+.1f}%",
    }


def _energy_shock(close: pd.DataFrame) -> dict:
    theme = "ENERGY_SHOCK"
    xle_1d = _return(close, "XLE", 1)
    spy_1d = _return(close, "SPY", 1)
    xle_3d = _return(close, "XLE", 3)
    spy_3d = _return(close, "SPY", 3)
    if None in (xle_1d, spy_1d, xle_3d, spy_3d):
        return _empty_item(theme, "insufficient price data")
    rel1 = xle_1d - spy_1d
    rel3 = xle_3d - spy_3d
    conditions = [rel1 > 1.5, rel3 > 3.0]
    status = _status_from_conditions(conditions)
    score = max(rel1, 0) / 1.5 + max(rel3, 0) / 3.0
    meta = THEME_CATALOG[theme]
    return {
        **_empty_item(theme),
        "status": status,
        "severity": _severity(status, score),
        "summary": meta["summary"] if status != "inactive" else "能源冲击未被价格确认。",
        "price_confirmed": status == "active",
        "confidence": round(min(0.9, 0.3 + score / 5), 2) if status != "inactive" else 0.0,
        "metrics": {"xle_vs_spy_1d_pct": round(rel1, 2), "xle_vs_spy_3d_pct": round(rel3, 2)},
        "action_hint": meta["action_hint"] if status == "active" else "watch_only",
        "reason": f"XLE-SPY 1日 {rel1:+.1f}%, 3日 {rel3:+.1f}%",
    }


def _credit_liquidity_shock(close: pd.DataFrame) -> dict:
    theme = "CREDIT_LIQUIDITY_SHOCK"
    iwm_1d = _return(close, "IWM", 1)
    spy_1d = _return(close, "SPY", 1)
    hyg_1d = _return(close, "HYG", 1)
    if None in (iwm_1d, spy_1d, hyg_1d):
        return _empty_item(theme, "insufficient price data")
    small_rel = iwm_1d - spy_1d
    conditions = [small_rel < -1.0, hyg_1d < -0.4]
    status = _status_from_conditions(conditions)
    score = max(-small_rel, 0) + max(-hyg_1d, 0)
    meta = THEME_CATALOG[theme]
    return {
        **_empty_item(theme),
        "status": status,
        "severity": _severity(status, score),
        "summary": meta["summary"] if status != "inactive" else "信用/流动性冲击未被价格确认。",
        "price_confirmed": status == "active",
        "confidence": round(min(0.9, 0.3 + score / 5), 2) if status != "inactive" else 0.0,
        "metrics": {"iwm_vs_spy_1d_pct": round(small_rel, 2), "hyg_1d_pct": round(hyg_1d, 2)},
        "action_hint": meta["action_hint"] if status == "active" else "watch_only",
        "reason": f"IWM-SPY {small_rel:+.1f}%, HYG {hyg_1d:+.1f}%",
    }


def get_narrative_radar(force_refresh: bool = False) -> dict:
    if not force_refresh:
        cached = _load_cache()
        if cached:
            return cached

    symbols = sorted(
        set(AI_HARDWARE_SYMBOLS)
        | set(AI_SOFTWARE_SYMBOLS)
        | {"SPY", "QQQ", "TLT", "XLE", "IWM", "HYG"}
    )
    try:
        close = _fetch_closes(symbols)
        if close.empty:
            raise RuntimeError("empty price data")
        items = [
            _ai_compute_oversupply(close),
            _rate_shock(close),
            _energy_shock(close),
            _credit_liquidity_shock(close),
        ]
    except Exception as e:
        items = [_empty_item(theme, f"radar unavailable: {e}") for theme in THEME_CATALOG]

    order = {"active": 0, "watch": 1, "inactive": 2}
    items.sort(key=lambda x: (order.get(x.get("status"), 9), x.get("theme", "")))
    active_count = sum(1 for x in items if x.get("status") == "active")
    watch_count = sum(1 for x in items if x.get("status") == "watch")
    radar = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "active_count": active_count,
        "watch_count": watch_count,
        "items": items,
    }
    _save_cache(radar)
    return radar
