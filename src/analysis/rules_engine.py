from __future__ import annotations
"""
Pure-Python rules signal for watchlist stocks not covered by Scout's scan universe.

Rex uses this as a fallback when a watchlist symbol has no Scout score in scan_cache.
Output format matches ai_analyst.analyze() so Rex's watchlist path needs no change.
No Claude calls — zero API cost.
"""


def rules_signal(symbol: str) -> dict | None:
    """
    Compute a deterministic BUY/HOLD signal using the same entry rules as Scout Layer 1.
    Returns dict compatible with trade_agent watchlist path, or None on data error.
    """
    try:
        from src.monitor.price_monitor import get_quote, get_ohlcv
        from src.analysis.technical_indicators import compute_all
        from src.analysis.position_sizer import compute_structured_stop
        from src.monitor.sp500_scanner import compute_kline_patterns

        quote = get_quote(symbol)
        ohlcv = get_ohlcv(symbol)
        if ohlcv is None or len(ohlcv) < 20:
            return None

        price = float(quote.get("price") or 0)
        if not price:
            return None

        indicators = compute_all(ohlcv)
        rsi  = float(indicators.get("rsi") or 50)
        ma20 = indicators.get("ma20")
        atr  = indicators.get("atr")
        vs_ma20_pct = ((price - ma20) / ma20 * 100) if ma20 else 0.0

        avg_vol = ohlcv["Volume"].rolling(20).mean().iloc[-1]
        cur_vol = ohlcv["Volume"].iloc[-1]
        volume_ratio = float(cur_vol / avg_vol) if avg_vol else 1.0

        kline         = compute_kline_patterns(ohlcv)
        candle_quality = kline.get("candle_quality", 0)
        today_bull     = kline.get("today_bull", False)

        momentum_5d = 0.0
        if len(ohlcv) >= 6:
            momentum_5d = (price / float(ohlcv["Close"].iloc[-6]) - 1) * 100

        # ── Entry rules (mirror Scout Layer 1 + Rex _strict_entry_ok) ────────
        rsi_ok    = 35 <= rsi <= 60
        ma20_ok   = -2.0 <= vs_ma20_pct <= 5.0
        vol_ok    = volume_ratio >= 1.0
        bull_ok   = today_bull
        candle_ok = candle_quality >= 0

        # ── Confidence score (0–100) ──────────────────────────────────────────
        score = 0
        if 42 <= rsi <= 58:        score += 25
        elif rsi_ok:               score += 15
        if -1 <= vs_ma20_pct <= 3: score += 20
        elif ma20_ok:              score += 10
        if volume_ratio >= 1.5:    score += 20
        elif vol_ok:               score += 10
        if candle_quality == 2:    score += 20
        elif candle_quality == 1:  score += 10
        if momentum_5d > 0:        score += 15

        all_rules_pass = rsi_ok and ma20_ok and vol_ok and bull_ok and candle_ok
        signal     = "BUY" if (all_rules_pass and score >= 50) else "HOLD"
        confidence = round(score / 100, 2)

        stop_loss    = compute_structured_stop(price, ma20, atr)
        target_price = round(price * 1.08, 2)  # default 8% swing target

        reasoning = (
            f"Rules engine: RSI={rsi:.0f}, vs_MA20={vs_ma20_pct:+.1f}%, "
            f"vol_ratio={volume_ratio:.1f}x, candle={candle_quality}, today_bull={today_bull}"
        )

        return {
            "signal":         signal,
            "confidence":     confidence,
            "stop_loss":      stop_loss,
            "target_price":   target_price,
            "reasoning":      reasoning,
            "price":          price,
            "rsi":            rsi,
            "vs_ma20_pct":    vs_ma20_pct,
            "momentum_5d":    momentum_5d,
            "volume_ratio":   volume_ratio,
            "candle_quality": candle_quality,
            "today_bull":     today_bull,
            "source":         "rules_engine",
        }

    except Exception as e:
        print(f"[rules_engine] {symbol} error: {e}")
        return None
