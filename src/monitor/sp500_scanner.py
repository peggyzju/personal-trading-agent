from __future__ import annotations
import pandas as pd
import yfinance as yf
import numpy as np
from src.analysis.technical_indicators import compute_all


# ── Stock Universe ────────────────────────────────────────────────────────────

def get_sp500_tickers() -> list[str]:
    try:
        table = pd.read_html("https://en.wikipedia.org/wiki/List_of_S%26P_500_companies")[0]
        return table["Symbol"].str.replace(".", "-", regex=False).tolist()
    except Exception:
        return [
            "AAPL","MSFT","NVDA","AMZN","GOOGL","META","TSLA","BRK-B","UNH","JPM",
            "V","XOM","LLY","JNJ","MA","AVGO","PG","HD","MRK","COST","ABBV","CVX",
            "KO","PEP","BAC","TMO","CSCO","MCD","ACN","ORCL","ABT","CRM","ADBE",
            "DHR","NKE","LIN","DIS","TXN","PM","NFLX","RTX","UPS","INTU","QCOM",
            "AMD","SPGI","AMGN","HON","CAT","SBUX","LOW","GS","BA","ELV","AXP",
        ]


def get_nasdaq100_tickers() -> list[str]:
    """Fetch NASDAQ-100 components from Wikipedia. Adds tech/growth coverage beyond S&P 500."""
    try:
        table = pd.read_html("https://en.wikipedia.org/wiki/Nasdaq-100")[4]
        for col in ("Ticker", "Symbol", "Ticker symbol"):
            if col in table.columns:
                return table[col].str.replace(".", "-", regex=False).tolist()
    except Exception:
        pass
    return [
        "AAPL","MSFT","NVDA","AMZN","META","TSLA","GOOGL","GOOG","AVGO","COST",
        "NFLX","AMD","ADBE","QCOM","INTU","CSCO","AMAT","MU","MRVL","KLAC",
        "LRCX","CDNS","SNPS","MELI","ASML","ABNB","DDOG","CRWD","PANW","ZS",
        "TEAM","WDAY","SNOW","OKTA","DOCU","ZM","SPLK","VEEV","TTD","NTNX",
        "PSTG","EXPE","SGEN","ILMN","BIIB","GILD","REGN","VRTX","IDXX","ALGN",
        "DLTR","KDP","MNST","PAYX","FAST","ODFL","CSGP","ANSS","ENPH","FSLR",
    ]


# ── Layer 2: High-growth mid-caps outside typical S&P 500 / NASDAQ-100 coverage
LAYER2_TICKERS: list[str] = [
    # Original coverage
    "SOUN","IONQ","BBAI","RKLB","LUNR","BTDR",
    "WOLF","ACLS","ONTO","AEHR","FORM",
    "KTOS","CACI",
    "GTLB","MNDY","BILL",
    "AFRM","UPST","SOFI","HOOD","MSTR",
    "RXRX","CRSP","BEAM",
    "ARRY","CHPT","EVGO",
    "CAVA","MOD","ARM",
    # Education tech
    "COUR","DUOL","INST",
    # Mid-cap SaaS / consumer tech
    "BRZE","IOT","HIMS","BROS",
    # Biotech / gene editing
    "NTLA","VERV","EDIT","FATE","NVCR",
    # Clean energy
    "STEM","PLUG","BLNK","BE",
    # Quantum / AI hardware
    "QUBT","RGTI",
    # Fintech
    "DAVE","MQ","STEP",
    # Space / defense
    "ASTS","PL",
    # Cybersecurity
    "TENB","RPD",
    # Air mobility
    "JOBY","ACHR",
    # 中概股 ADR（流动性 >5M 日均成交量）
    "NIO","XPEV","LI","FUTU","BILI","EDU","TCOM","VIPS",
]


def get_scan_universe(include_dynamic: bool = True) -> list[str]:
    """
    Build scan universe: S&P 500 + Nasdaq-100 + Layer2 + (optionally) today's
    dynamic tickers discovered by Scout.
    """
    sp500  = get_sp500_tickers()
    ndq100 = get_nasdaq100_tickers()
    layer2 = LAYER2_TICKERS

    # Load today's Scout-discovered dynamic tickers
    dynamic: list[str] = []
    if include_dynamic:
        try:
            from src.monitor.scout import get_dynamic_tickers
            dynamic = get_dynamic_tickers()
        except Exception:
            pass

    seen: set[str] = set()
    combined: list[str] = []
    for sym in sp500 + ndq100 + layer2 + dynamic:
        if sym not in seen:
            seen.add(sym)
            combined.append(sym)

    l2_unique  = len([s for s in layer2   if s not in set(sp500 + ndq100)])
    dyn_unique = len([s for s in dynamic  if s not in set(sp500 + ndq100 + layer2)])
    print(f"[scanner] Universe: {len(sp500)} S&P500 + "
          f"{len([s for s in ndq100 if s not in set(sp500)])} NDQ100-unique "
          f"+ {l2_unique} Layer2-unique "
          f"+ {dyn_unique} dynamic "
          f"= {len(combined)} total")
    return combined


# ── K-line pattern detection ──────────────────────────────────────────────────

def compute_kline_patterns(df: pd.DataFrame) -> dict:
    """
    Detect candlestick patterns from the last 3 bars.
    Returns a dict with pattern labels and a candle_quality score (-2 to +2).

    Patterns detected:
      hammer         — 下影线长，底部支撑信号
      bullish_engulf — 今日阳线吃掉昨日阴线，反转信号
      bearish_engulf — 今日阴线吃掉昨日阳线，转弱信号
      doji           — 实体极小，多空平衡，观望
      strong_bull    — 大阳线，买方强势
      strong_bear    — 大阴线，卖方强势
      pullback_bull  — 近3日缩量回调后今日阳线（swing 理想入场）
      volume_dry     — 近3日成交量逐步萎缩（健康回调）
      volume_expand_down — 下跌伴随放量（危险）
    """
    if len(df) < 4:
        return {"candle_quality": 0, "patterns": [], "candle_desc": "insufficient data"}

    o  = df["Open"].values
    h  = df["High"].values
    l  = df["Low"].values
    c  = df["Close"].values
    v  = df["Volume"].values

    # Today = -1, yesterday = -2, two days ago = -3
    o0, h0, l0, c0, v0 = float(o[-1]), float(h[-1]), float(l[-1]), float(c[-1]), float(v[-1])
    o1, h1, l1, c1, v1 = float(o[-2]), float(h[-2]), float(l[-2]), float(c[-2]), float(v[-2])
    o2, h2, l2, c2, v2 = float(o[-3]), float(h[-3]), float(l[-3]), float(c[-3]), float(v[-3])

    vol_avg = float(np.mean(v[-22:-2])) if len(v) >= 22 else float(np.mean(v[:-1]))

    range0  = h0 - l0 if h0 > l0 else 0.0001
    body0   = abs(c0 - o0)
    body1   = abs(c1 - o1)
    upper0  = h0 - max(c0, o0)   # 上影线
    lower0  = min(c0, o0) - l0   # 下影线

    body0_pct = body0 / o0 * 100
    body1_pct = body1 / o1 * 100

    is_bull0 = c0 > o0
    is_bull1 = c1 > o1
    is_bull2 = c2 > o2

    patterns = []
    score    = 0

    # ── 今日 K 线形态 ────────────────────────────────────────────────────────

    # 大阳线：实体 > 1.5%，收盘在上半部
    if is_bull0 and body0_pct > 1.5 and c0 > (l0 + range0 * 0.6):
        patterns.append("strong_bull")
        score += 2

    # 大阴线：实体 > 1.5%，收盘在下半部
    elif not is_bull0 and body0_pct > 1.5 and c0 < (l0 + range0 * 0.4):
        patterns.append("strong_bear")
        score -= 2

    # 锤子线：下影线 > 2倍实体，实体在上半段
    # 回测显示锤子线单独期望值 -1.95%，仅作轻微加分；需结合其他信号
    if lower0 > body0 * 2 and lower0 > upper0 * 2 and body0_pct < 2.0:
        patterns.append("hammer")
        score += 1 if is_bull0 else 0   # 回测降级：阳线+1，阴线不加分

    # 十字星：实体极小（< 0.3%）
    # 回测显示 doji 期望值 -2.17%，明确下调为 -1
    if body0_pct < 0.3:
        patterns.append("doji")
        score -= 1  # 方向不明，观望信号，不适合 swing 入场

    # ── 两日组合形态 ─────────────────────────────────────────────────────────

    # 看涨吞没：今阳 > 昨阴，实体吞没
    if is_bull0 and not is_bull1 and o0 <= c1 and c0 >= o1 and body0 > body1:
        patterns.append("bullish_engulf")
        score += 2

    # 看跌吞没：今阴 > 昨阳，实体吞没
    if not is_bull0 and is_bull1 and o0 >= c1 and c0 <= o1 and body0 > body1:
        patterns.append("bearish_engulf")
        score -= 2

    # ── 3日成交量形态 ─────────────────────────────────────────────────────────

    # 缩量回调：近3日成交量逐步降低（v2 > v1 > v0），今日阳线
    # 回测显示期望值 -1.28%，独立信号不足，降为 +1（需结合 RSI/MA20）
    if v2 > v1 > v0 and is_bull0 and v0 < vol_avg:
        patterns.append("pullback_bull")
        score += 1   # 回测降级：+2 → +1

    # 成交量萎缩（不管方向）：卖压减弱，维持 +1
    if v0 < vol_avg * 0.8 and v1 < vol_avg * 0.8:
        patterns.append("volume_dry")
        score += 1

    # 下跌放量：今日阴线 + 成交量 > 1.3x 均量
    # 回测 +0.91% 来自牛市 buy-the-dip 效应，样本仅 60 笔；
    # 全自动系统无法区分机构出逃 vs 情绪抛售，坚持右侧交易，硬性惩罚
    if not is_bull0 and v0 > vol_avg * 1.3:
        patterns.append("volume_expand_down")
        score -= 2   # 回滚：-2（左侧猜底禁止入场）

    # ── 描述文字（给 AI 看）──────────────────────────────────────────────────
    today_desc = f"{'阳线' if is_bull0 else '阴线'} 实体{body0_pct:.1f}% 振幅{range0/o0*100:.1f}%"
    vol_desc   = f"今量{v0/vol_avg:.1f}x均量"
    trend3d    = (
        "连续3日阳线" if (is_bull0 and is_bull1 and is_bull2) else
        "连续3日阴线" if (not is_bull0 and not is_bull1 and not is_bull2) else
        "近3日震荡"
    )

    candle_desc = f"{today_desc} | {vol_desc} | {trend3d}"
    if patterns:
        label_map = {
            "strong_bull":        "大阳线",
            "strong_bear":        "大阴线",
            "hammer":             "锤子线",
            "doji":               "十字星",
            "bullish_engulf":     "看涨吞没",
            "bearish_engulf":     "看跌吞没",
            "pullback_bull":      "缩量回调后阳线",
            "volume_dry":         "成交量萎缩",
            "volume_expand_down": "下跌放量⚠️",
        }
        candle_desc += " | " + " + ".join(label_map.get(p, p) for p in patterns)

    return {
        "candle_quality": max(-2, min(2, score)),   # clamp to -2~+2
        "patterns":       patterns,
        "candle_desc":    candle_desc,
        "today_bull":     is_bull0,
        "body_pct":       round(body0_pct, 2),
        "vol_vs_avg":     round(v0 / vol_avg, 2) if vol_avg else 1.0,
    }


# ── Technical scoring ─────────────────────────────────────────────────────────

def compute_technicals(df: pd.DataFrame) -> dict:
    """Compute full technical picture from OHLCV DataFrame."""
    closes  = df["Close"].dropna()
    volumes = df["Volume"].dropna()
    if len(closes) < 6:
        return {}

    price_now = float(closes.iloc[-1])
    price_5d  = float(closes.iloc[-6])  if len(closes) >= 6  else float(closes.iloc[0])
    price_1m  = float(closes.iloc[-22]) if len(closes) >= 22 else float(closes.iloc[0])
    price_3m  = float(closes.iloc[-63]) if len(closes) >= 63 else float(closes.iloc[0])
    momentum_5d  = (price_now - price_5d)  / price_5d  * 100
    momentum_1m  = (price_now - price_1m)  / price_1m  * 100
    momentum_3m  = (price_now - price_3m)  / price_3m  * 100

    vol_prev = float(volumes.iloc[-2]) if len(volumes) >= 2 else float(volumes.iloc[-1])
    vol_avg  = float(volumes.iloc[-22:-2].mean()) if len(volumes) >= 22 else float(volumes.iloc[:-1].mean())
    volume_ratio = vol_prev / vol_avg if vol_avg > 0 else 1.0

    high_20d     = float(closes.iloc[-21:-1].max()) if len(closes) >= 21 else float(closes.max())
    near_breakout = price_now >= high_20d * 0.98

    indicators = compute_all(df)
    rsi = indicators.get("rsi", 50.0)
    vs_ma20 = indicators.get("vs_ma20_pct") or 0.0

    # ── Factor 1: Momentum composite (封顶防追高) ────────────────────────────
    # RSI、MACD、5日动量、布林带 本质上都是"动量"的变体，合并后封顶 25 分，
    # 避免多重共线性导致市场暴涨时评分集体飙高。
    rsi_raw = (
        15 if 40 <= rsi <= 58 else   # ideal recovery zone
        8  if 35 <= rsi < 40  else   # slightly oversold
        8  if 58 < rsi <= 62  else   # slightly extended
        0  if 62 < rsi <= 67  else   # overheated
        -10                          # >67: penalise
    )
    mom5d_raw  = min(max(momentum_5d, 0), 8) * 1.5      # 5d momentum, capped
    macd_raw   = 10 if indicators.get("macd_bullish_cross") else 0
    bb_raw     = (5 if (indicators.get("bb_pct_b", 0.5) or 0) > 0.45
                       and (indicators.get("bb_pct_b", 0.5) or 0) < 0.85 else 0)

    momentum_composite = rsi_raw + mom5d_raw + macd_raw + bb_raw
    momentum_score = min(momentum_composite, 25)   # hard cap: all 4 momentum signals combined ≤ 25

    # ── Factor 2: Volume participation (独立因子，非动量) ─────────────────────
    volume_score = min(max(volume_ratio - 1, 0), 3) * 6   # max +18

    # ── Factor 3: MA20 structure position (价格结构，独立) ────────────────────
    ma20_score = (
        15 if 0 <= vs_ma20 <= 3 else   # ideal: just above MA20
        10 if 3 < vs_ma20 <= 5 else    # acceptable
        0  if 5 < vs_ma20 <= 8 else    # borderline
        -15                            # >8%: chasing, heavy penalty
    )

    # ── Factor 4: K-line pattern (价格形态，独立) ─────────────────────────────
    kline = compute_kline_patterns(df)
    candle_score = kline.get("candle_quality", 0) * 5   # -10 to +10

    # ── Anti-chasing penalties (对抗性因子，防鱼尾行情) ─────────────────────────
    atr_pct = indicators.get("atr_pct") or 2.0
    # ATR > 4%/day 说明波动率过高，可能处于主升浪末段
    atr_penalty   = max(0.0, (atr_pct - 4.0) * 3)
    # 5日涨幅 > 8% 说明斜率过陡，继续追高风险大
    slope_penalty = max(0.0, (momentum_5d - 8.0) * 2)

    score = (
        momentum_score
        + volume_score
        + ma20_score
        + candle_score
        - atr_penalty
        - slope_penalty
    )

    result = {
        "price":        round(price_now, 2),
        "momentum_5d":  round(momentum_5d, 2),
        "momentum_1m":  round(momentum_1m, 2),
        "momentum_3m":  round(momentum_3m, 2),
        "volume_ratio": round(volume_ratio, 2),
        "near_breakout": near_breakout,
        "rsi":          rsi,
        "tech_score":   round(score, 2),
        # K-line fields
        "candle_quality": kline.get("candle_quality"),
        "candle_desc":    kline.get("candle_desc"),
        "candle_patterns": kline.get("patterns", []),
        "today_bull":     kline.get("today_bull"),
        "body_pct":       kline.get("body_pct"),
        "vol_vs_avg":     kline.get("vol_vs_avg"),
    }
    for key in ("macd_bullish_cross", "macd_bearish_cross", "bb_pct_b",
                "vs_ma20_pct", "vs_ma50_pct", "vs_ma200_pct",
                "golden_cross", "atr", "atr_pct"):
        if key in indicators:
            result[key] = indicators[key]
    return result


def enrich_with_fundamentals(candidates: list[dict]) -> list[dict]:
    """Fetch P/E, market cap, beta, 52w range, company name & sector — parallel."""
    from concurrent.futures import ThreadPoolExecutor

    def fetch_one(c: dict) -> dict:
        try:
            info = yf.Ticker(c["symbol"]).info
            pe   = info.get("trailingPE") or info.get("forwardPE")
            mc   = info.get("marketCap")
            beta = info.get("beta")
            c["pe_ratio"]     = round(pe, 1) if pe else None
            c["market_cap"]   = int(mc) if mc else None
            c["beta"]         = round(beta, 2) if beta else None
            c["week52_high"]  = info.get("fiftyTwoWeekHigh")
            c["week52_low"]   = info.get("fiftyTwoWeekLow")
            c["company_name"] = info.get("longName") or info.get("shortName") or c["symbol"]
            c["sector"]       = info.get("sector", "")
            c["industry"]     = info.get("industry", "")
        except Exception:
            pass
        return c

    with ThreadPoolExecutor(max_workers=8) as pool:
        return list(pool.map(fetch_one, candidates))


def quick_screen(
    tickers: list[str],
    top_n: int = 25,
    progress_cb=None,
    force_symbols: set[str] | None = None,
) -> list[dict]:
    """
    Download 60d OHLCV per-ticker in parallel, compute technicals, return top_n.
    Uses individual Ticker.history() calls instead of batch download —
    single-ticker requests are fast and a failure only skips that one ticker.

    force_symbols: watchlist tickers that bypass the ma20_ok chase-filter so
    they always surface even during strong momentum days.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed
    WORKERS = 20   # parallel downloads
    TIMEOUT  = 20  # seconds per individual ticker
    all_results: list[dict] = []
    total = len(tickers)
    _force = force_symbols or set()

    print(f"[scanner] downloading {total} tickers individually ({WORKERS} parallel)…")

    def _fetch(symbol: str) -> dict | None:
        try:
            df = yf.Ticker(symbol).history(period="90d", auto_adjust=True)
            if df.empty or len(df) < 5:
                return None
            tech = compute_technicals(df)
            if not tech:
                return None
            rsi_ok    = tech["rsi"] < 60                      # aligned with Rex _strict_entry_ok
            ma20_ok   = (tech.get("vs_ma20_pct") or 0) <= 8.0 # filter chasers
            trend_ok  = tech["momentum_5d"] > -3              # allow mild pullbacks
            bull_ok   = tech.get("today_bull", False)          # 右侧交易：今日必须收阳
            # Watchlist stocks bypass ma20_ok — user explicitly tracks these
            passes = rsi_ok and trend_ok and bull_ok and (ma20_ok or symbol in _force)
            if passes:
                return {"symbol": symbol, **tech}
        except Exception:
            pass
        return None

    done = 0
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futures = {pool.submit(_fetch, sym): sym for sym in tickers}
        for fut in as_completed(futures, timeout=TIMEOUT * total / WORKERS + 60):
            done += 1
            try:
                result = fut.result(timeout=TIMEOUT)
                if result:
                    all_results.append(result)
            except Exception:
                pass
            if progress_cb and done % 20 == 0:
                progress_cb("downloading", done, total)

    if progress_cb:
        progress_cb("screening_done", total, total)

    passed = len(all_results)
    if passed == 0:
        print("[scanner] WARNING: 0 candidates passed technical filter")
    else:
        print(f"[scanner] {passed}/{total} passed momentum filter")

    all_results.sort(key=lambda x: x["tech_score"], reverse=True)
    return all_results[:top_n]
