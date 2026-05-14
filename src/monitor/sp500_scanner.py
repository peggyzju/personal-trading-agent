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
        # Column name varies — try common ones
        for col in ("Ticker", "Symbol", "Ticker symbol"):
            if col in table.columns:
                return table[col].str.replace(".", "-", regex=False).tolist()
    except Exception:
        pass
    # Static fallback — key NASDAQ-100 names (most overlap with S&P 500; duplicates handled in universe)
    return [
        "AAPL","MSFT","NVDA","AMZN","META","TSLA","GOOGL","GOOG","AVGO","COST",
        "NFLX","AMD","ADBE","QCOM","INTU","CSCO","AMAT","MU","MRVL","KLAC",
        "LRCX","CDNS","SNPS","MELI","ASML","ABNB","DDOG","CRWD","PANW","ZS",
        "TEAM","WDAY","SNOW","OKTA","DOCU","ZM","SPLK","VEEV","TTD","NTNX",
        "PSTG","EXPE","SGEN","ILMN","BIIB","GILD","REGN","VRTX","IDXX","ALGN",
        "DLTR","KDP","MNST","PAYX","FAST","ODFL","CSGP","ANSS","ENPH","FSLR",
    ]


# ── Layer 2: High-growth mid-caps outside typical S&P 500 / NASDAQ-100 coverage
# Focus: AI infrastructure, semis, defense tech, fintech, biotech, energy transition
# These are liquid (>$3/share, avg vol >300k) but smaller/newer than large-cap indices.
LAYER2_TICKERS: list[str] = [
    # AI & next-gen compute
    "SOUN",   # SoundHound AI — voice AI platform
    "IONQ",   # IonQ — quantum computing hardware
    "BBAI",   # BigBear.ai — defense/intelligence AI
    "RKLB",   # Rocket Lab — small satellite launch + space systems
    "LUNR",   # Intuitive Machines — lunar logistics
    "BTDR",   # Bitdeer — AI datacenter/mining infra

    # Semiconductors (mid-cap)
    "WOLF",   # Wolfspeed — silicon carbide (EV/power semis)
    "ACLS",   # Axcelis Technologies — ion implant systems
    "ONTO",   # Onto Innovation — semiconductor metrology
    "AEHR",   # Aehr Test Systems — wafer-level burn-in
    "FORM",   # FormFactor — probe cards

    # Defense & aerospace (beyond major primes)
    "KTOS",   # Kratos Defense — drones, satellites, missiles
    "CACI",   # CACI International — defense IT & intelligence

    # High-growth SaaS / platforms
    "GTLB",   # GitLab — DevSecOps platform
    "MNDY",   # Monday.com — work OS
    "BILL",   # Bill.com — SMB financial automation

    # Fintech / crypto-adjacent
    "AFRM",   # Affirm — BNPL / payments
    "UPST",   # Upstart — AI lending platform
    "SOFI",   # SoFi Technologies — neobank
    "HOOD",   # Robinhood Markets — retail brokerage
    "MSTR",   # MicroStrategy — BTC treasury / BI

    # Biotech (gene editing & AI drug discovery)
    "RXRX",   # Recursion Pharmaceuticals — AI drug discovery
    "CRSP",   # CRISPR Therapeutics — gene editing
    "BEAM",   # Beam Therapeutics — base editing

    # Energy transition & cleantech
    "ARRY",   # Array Technologies — solar tracker systems
    "CHPT",   # ChargePoint — EV charging network
    "EVGO",   # EVgo — fast-charge EV network

    # Consumer / other high-growth
    "CAVA",   # CAVA Group — Mediterranean fast-casual chain
    "MOD",    # Modine Manufacturing — thermal management (user holding)
    "ARM",    # Arm Holdings — CPU IP licensing (fabless semis)
]


def get_scan_universe() -> list[str]:
    """
    Combined scan universe: S&P 500 + NASDAQ-100 + Layer 2 curated mid-caps.
    Deduplicates while preserving order (S&P 500 first for priority).
    Returns ~600-700 tickers.
    """
    sp500   = get_sp500_tickers()
    ndq100  = get_nasdaq100_tickers()
    layer2  = LAYER2_TICKERS

    seen: set[str] = set()
    combined: list[str] = []
    for sym in sp500 + ndq100 + layer2:
        if sym not in seen:
            seen.add(sym)
            combined.append(sym)

    print(f"[scanner] Universe: {len(sp500)} S&P500 + {len([s for s in ndq100 if s not in set(sp500)])} NDQ100-unique "
          f"+ {len([s for s in layer2 if s not in seen - set(layer2)])} Layer2-unique = {len(combined)} total")
    return combined


# ── Technical scoring ─────────────────────────────────────────────────────────

def compute_technicals(df: pd.DataFrame) -> dict:
    """Compute full technical picture from OHLCV DataFrame."""
    closes = df["Close"].dropna()
    volumes = df["Volume"].dropna()
    if len(closes) < 6:
        return {}

    price_now = float(closes.iloc[-1])

    # 5-day momentum
    price_5d = float(closes.iloc[-6]) if len(closes) >= 6 else float(closes.iloc[0])
    momentum_5d = (price_now - price_5d) / price_5d * 100

    # Volume ratio: use previous COMPLETED day (iloc[-2]) to avoid partial intraday bar
    vol_prev = float(volumes.iloc[-2]) if len(volumes) >= 2 else float(volumes.iloc[-1])
    vol_avg = float(volumes.iloc[-22:-2].mean()) if len(volumes) >= 22 else float(volumes.iloc[:-1].mean())
    volume_ratio = vol_prev / vol_avg if vol_avg > 0 else 1.0

    # 20-day breakout (use completed bars only)
    high_20d = float(closes.iloc[-21:-1].max()) if len(closes) >= 21 else float(closes.max())
    near_breakout = price_now >= high_20d * 0.98

    # Full indicator suite
    indicators = compute_all(df)
    rsi = indicators.get("rsi", 50.0)

    # Composite tech score (0–100 scale)
    score = (
        min(max(momentum_5d, 0), 10) * 3.5               # up to 35 pts: 5-day momentum
        + min(max(volume_ratio - 1, 0), 3) * 10 * 0.25   # up to 7.5 pts: volume surge
        + (15 if near_breakout else 0)                    # 15 pts: near 20-day high
        + (10 if indicators.get("macd_bullish_cross") else 0)   # 10 pts: MACD bullish cross
        + (10 if indicators.get("bb_pct_b", 0.5) > 0.55 else 0)  # 10 pts: above BB midline
        + (10 if (indicators.get("vs_ma20_pct") or 0) > 0
                 and (indicators.get("vs_ma50_pct") or 0) > 0 else 0)  # 10 pts: aligned above MAs
        + max(0, (70 - rsi)) * 0.5                        # up to 10 pts: not overbought
    )

    result = {
        "price": round(price_now, 2),
        "momentum_5d": round(momentum_5d, 2),
        "volume_ratio": round(volume_ratio, 2),
        "near_breakout": near_breakout,
        "rsi": rsi,
        "tech_score": round(score, 2),
    }
    for key in ("macd_bullish_cross", "macd_bearish_cross", "bb_pct_b",
                "vs_ma20_pct", "vs_ma50_pct", "vs_ma200_pct",
                "golden_cross", "atr", "atr_pct"):
        if key in indicators:
            result[key] = indicators[key]
    return result


def enrich_with_fundamentals(candidates: list[dict]) -> list[dict]:
    """Fetch P/E, market cap, beta, 52w range, company name & sector for top candidates."""
    from concurrent.futures import ThreadPoolExecutor

    def fetch_one(c: dict) -> dict:
        try:
            info = yf.Ticker(c["symbol"]).info
            pe = info.get("trailingPE") or info.get("forwardPE")
            mc = info.get("marketCap")
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


def quick_screen(tickers: list[str], top_n: int = 25) -> list[dict]:
    """
    Batch-download 60d OHLCV for all tickers, compute technicals,
    return top_n ranked by tech_score.
    Handles large universes (600+ tickers) by chunking the download.
    """
    CHUNK = 500   # yfinance is stable up to ~500 tickers per call
    all_results: list[dict] = []

    chunks = [tickers[i:i + CHUNK] for i in range(0, len(tickers), CHUNK)]
    for chunk in chunks:
        try:
            raw = yf.download(
                chunk,
                period="60d",
                group_by="ticker",
                auto_adjust=True,
                threads=True,
                progress=False,
            )
        except Exception as e:
            print(f"[scanner] batch download error (chunk {len(chunk)} tickers): {e}")
            continue

        for symbol in chunk:
            try:
                if len(chunk) == 1:
                    df = raw
                else:
                    df = raw[symbol] if symbol in raw.columns.get_level_values(0) else pd.DataFrame()
                if df.empty or len(df) < 5:
                    continue
                tech = compute_technicals(df)
                if not tech:
                    continue
                if tech["momentum_5d"] > 0 and tech["rsi"] < 75:
                    all_results.append({"symbol": symbol, **tech})
            except Exception:
                continue

    all_results.sort(key=lambda x: x["tech_score"], reverse=True)
    return all_results[:top_n]
