"""
Trade Replay Analysis
=====================
Compare how the OLD strategy (3% stop, no entry filter) vs NEW strategy
(5% stop + RSI<60 + within 5% of MA20) would have performed on actual
executed trades from the last N days.

Usage:
    python scripts/trade_replay.py [--days 7]
"""
from __future__ import annotations

import os, sys, argparse
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import yfinance as yf
from dotenv import load_dotenv

load_dotenv()
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import alpaca_trade_api as tradeapi


# ── Helpers ──────────────────────────────────────────────────────────────────

def _scalar(val):
    """Safely extract a scalar from a pd.Series or plain value."""
    if isinstance(val, pd.Series):
        return float(val.iloc[0])
    return float(val)


def _get_ohlcv(sym: str, start: str, end: str) -> pd.DataFrame:
    """Download daily OHLCV for sym, always returns a flat (single-level) DataFrame."""
    df = yf.download(sym, start=start, end=end, auto_adjust=True, progress=False)
    # yfinance sometimes returns multi-level columns even for single symbol
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    return df


def _compute_rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    rs = gain / loss.replace(0, np.nan)
    return 100 - 100 / (1 + rs)


def _compute_ma(close: pd.Series, period: int) -> pd.Series:
    return close.rolling(period).mean()


def _indicators_at_date(df: pd.DataFrame, entry_date: pd.Timestamp) -> dict:
    """Compute RSI(14) and MA20 as-of entry_date (using only prior data)."""
    idx = df.index.searchsorted(entry_date, side="right")
    if idx == 0:
        return {}
    sub = df.iloc[:idx]
    if len(sub) < 20:
        return {}
    close = sub["Close"]
    rsi_ser = _compute_rsi(close)
    ma20_ser = _compute_ma(close, 20)
    last_close = _scalar(close.iloc[-1])
    last_rsi   = _scalar(rsi_ser.iloc[-1])
    last_ma20  = _scalar(ma20_ser.iloc[-1])
    vs_ma20_pct = (last_close - last_ma20) / last_ma20 * 100 if last_ma20 else None
    return {
        "rsi": round(last_rsi, 1),
        "ma20": round(last_ma20, 2),
        "vs_ma20_pct": round(vs_ma20_pct, 1) if vs_ma20_pct is not None else None,
        "close_at_entry": round(last_close, 2),
    }


def _simulate_trade(
    df: pd.DataFrame,
    entry_date: pd.Timestamp,
    entry_price: float,
    stop_pct: float,
    hold_days: int = 10,
    target_pct: float = 0.08,
    slippage_pct: float = 0.003,
) -> dict:
    """Simulate a trade starting the day after entry_date."""
    entry_price_with_slip = entry_price * (1 + slippage_pct)
    stop_level  = entry_price_with_slip * (1 - stop_pct)
    target_level = entry_price_with_slip * (1 + target_pct)

    # rows after entry
    mask = df.index > entry_date
    future = df[mask].head(hold_days)

    for i, (dt, row) in enumerate(future.iterrows()):
        low   = _scalar(row["Low"])
        high  = _scalar(row["High"])
        close = _scalar(row["Close"])
        days_held = i + 1

        if low <= stop_level:
            exit_p = stop_level * (1 - slippage_pct)
            pnl    = (exit_p - entry_price_with_slip) / entry_price_with_slip * 100
            return {"exit_reason": "stop_loss",  "pnl_pct": round(pnl, 2), "days_held": days_held, "exit_date": str(dt.date())}
        elif high >= target_level:
            exit_p = target_level * (1 - slippage_pct)
            pnl    = (exit_p - entry_price_with_slip) / entry_price_with_slip * 100
            return {"exit_reason": "target_hit", "pnl_pct": round(pnl, 2), "days_held": days_held, "exit_date": str(dt.date())}

    # time exit
    if len(future) == 0:
        return {"exit_reason": "still_open", "pnl_pct": None, "days_held": 0, "exit_date": None}
    last_close = _scalar(future.iloc[-1]["Close"])
    pnl = (last_close * (1 - slippage_pct) - entry_price_with_slip) / entry_price_with_slip * 100
    return {"exit_reason": "time_exit", "pnl_pct": round(pnl, 2), "days_held": len(future), "exit_date": str(future.index[-1].date())}


# ── Main ──────────────────────────────────────────────────────────────────────

def main(lookback_days: int = 10):
    api_key    = os.environ.get("ALPACA_API_KEY")
    api_secret = os.environ.get("ALPACA_SECRET_KEY")
    base_url   = os.environ.get("ALPACA_BASE_URL", "https://paper-api.alpaca.markets")

    api = tradeapi.REST(api_key, api_secret, base_url)

    since_dt  = datetime.now(timezone.utc) - timedelta(days=lookback_days)
    since_iso = since_dt.isoformat()
    orders = api.list_orders(
        status="closed",
        after=since_iso,
        limit=200,
        direction="desc",
    )

    # Keep only filled buy orders
    buys = [
        o for o in orders
        if o.side == "buy" and o.status == "filled" and o.filled_avg_price
    ]
    print(f"\nFound {len(buys)} filled buy orders in the last {lookback_days} days.\n")

    if not buys:
        print("No buy orders to replay.")
        return

    # ── Fetch price data for all symbols (extra 60 days back for indicators) ──
    syms = list({o.symbol for o in buys})
    hist_start = (since_dt - timedelta(days=80)).strftime("%Y-%m-%d")
    hist_end   = datetime.now().strftime("%Y-%m-%d")

    price_data: dict[str, pd.DataFrame] = {}
    for sym in syms:
        try:
            df = _get_ohlcv(sym, hist_start, hist_end)
            if not df.empty:
                price_data[sym] = df
        except Exception as e:
            print(f"  [skip] {sym}: {e}")

    # ── Replay each trade ─────────────────────────────────────────────────────
    results = []
    for o in buys:
        sym   = o.symbol
        ep    = float(o.filled_avg_price)
        # filled_at may be a string or datetime
        filled_at = o.filled_at
        if isinstance(filled_at, str):
            filled_at = pd.Timestamp(filled_at)
        edate = pd.Timestamp(filled_at).tz_localize(None).normalize()

        df = price_data.get(sym)
        if df is None or df.empty:
            continue

        indic = _indicators_at_date(df, edate)
        rsi         = indic.get("rsi")
        vs_ma20_pct = indic.get("vs_ma20_pct")

        # Strict entry check (new strategy)
        strict_blocked = False
        block_reason   = ""
        if rsi is not None and rsi >= 60:
            strict_blocked = True
            block_reason   = f"RSI={rsi} ≥ 60"
        elif vs_ma20_pct is not None and vs_ma20_pct > 5.0:
            strict_blocked = True
            block_reason   = f"vs_MA20={vs_ma20_pct:+.1f}% > 5%"

        # Simulate OLD strategy (3% stop)
        old_sim = _simulate_trade(df, edate, ep, stop_pct=0.03)

        # Simulate NEW strategy (5% stop); None if blocked
        if strict_blocked:
            new_sim = {"exit_reason": "blocked", "pnl_pct": None, "days_held": 0, "exit_date": None}
        else:
            new_sim = _simulate_trade(df, edate, ep, stop_pct=0.05)

        results.append({
            "symbol":        sym,
            "entry_date":    str(edate.date()),
            "entry_price":   ep,
            "rsi_at_entry":  rsi,
            "vs_ma20_pct":   vs_ma20_pct,
            "strict_blocked": strict_blocked,
            "block_reason":  block_reason,
            # old
            "old_exit":      old_sim["exit_reason"],
            "old_pnl_pct":   old_sim["pnl_pct"],
            "old_days":      old_sim["days_held"],
            # new
            "new_exit":      new_sim["exit_reason"],
            "new_pnl_pct":   new_sim["pnl_pct"],
            "new_days":      new_sim["days_held"],
        })

    # ── Print trade-by-trade table ────────────────────────────────────────────
    print(f"{'SYM':<6} {'Date':<11} {'EP':>7} {'RSI':>5} {'vMA20':>7} {'Blocked':<9} "
          f"{'OLD exit':<12} {'OLD %':>7}  {'NEW exit':<12} {'NEW %':>7}")
    print("-" * 100)

    old_pnls, new_pnls = [], []
    for r in sorted(results, key=lambda x: x["entry_date"]):
        old_p = f"{r['old_pnl_pct']:+.2f}%" if r["old_pnl_pct"] is not None else "  open"
        new_p = f"{r['new_pnl_pct']:+.2f}%" if r["new_pnl_pct"] is not None else ("blocked" if r["strict_blocked"] else "  open")
        rsi_s = f"{r['rsi_at_entry']:.0f}" if r["rsi_at_entry"] else "  -"
        vma_s = f"{r['vs_ma20_pct']:+.1f}%" if r["vs_ma20_pct"] is not None else "    -"
        blk   = f"✗ {r['block_reason']}" if r["strict_blocked"] else "✓ pass"
        print(f"{r['symbol']:<6} {r['entry_date']:<11} {r['entry_price']:>7.2f} "
              f"{rsi_s:>5} {vma_s:>7} {blk:<20} "
              f"{r['old_exit']:<12} {old_p:>8}  {r['new_exit']:<12} {new_p:>8}")
        if r["old_pnl_pct"] is not None:
            old_pnls.append(r["old_pnl_pct"])
        if r["new_pnl_pct"] is not None:
            new_pnls.append(r["new_pnl_pct"])

    # ── Summary ───────────────────────────────────────────────────────────────
    print("\n" + "=" * 80)
    print("SUMMARY")
    print("=" * 80)
    blocked_count = sum(1 for r in results if r["strict_blocked"])
    print(f"  Total trades replayed : {len(results)}")
    print(f"  Would be BLOCKED (new): {blocked_count}  ({blocked_count/len(results)*100:.0f}%)")
    print()

    def _stats(pnls, label):
        if not pnls:
            print(f"  {label}: no closed trades")
            return
        wins   = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p <= 0]
        wr     = len(wins) / len(pnls) * 100
        avg_w  = np.mean(wins)  if wins   else 0
        avg_l  = np.mean(losses) if losses else 0
        pf     = abs(sum(wins) / sum(losses)) if sum(losses) else float("inf")
        total  = sum(pnls)
        print(f"  {label}")
        print(f"    Trades    : {len(pnls)}  (wins={len(wins)}, losses={len(losses)})")
        print(f"    Win rate  : {wr:.1f}%")
        print(f"    Avg win   : {avg_w:+.2f}%    Avg loss: {avg_l:+.2f}%")
        print(f"    Profit fac: {pf:.2f}")
        print(f"    Sum P&L   : {total:+.2f}%")
        print()

    _stats(old_pnls, "OLD strategy (3% stop, no entry filter)")
    _stats(new_pnls, "NEW strategy (5% stop, RSI<60 + within 5% MA20)")

    print("Note: 'blocked' trades are skipped by new strategy → capital preserved.")
    print("      Compare new_pnl to old_pnl only on *passing* trades.\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=10, help="Lookback days (default 10)")
    args = parser.parse_args()
    main(lookback_days=args.days)
