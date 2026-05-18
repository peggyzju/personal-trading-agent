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


# ── Technical scoring ─────────────────────────────────────────────────────────

def compute_technicals(df: pd.DataFrame) -> dict:
    """Compute full technical picture from OHLCV DataFrame."""
    closes  = df["Close"].dropna()
    volumes = df["Volume"].dropna()
    if len(closes) < 6:
        return {}

    price_now = float(closes.iloc[-1])
    price_5d  = float(closes.iloc[-6]) if len(closes) >= 6 else float(closes.iloc[0])
    momentum_5d = (price_now - price_5d) / price_5d * 100

    vol_prev = float(volumes.iloc[-2]) if len(volumes) >= 2 else float(volumes.iloc[-1])
    vol_avg  = float(volumes.iloc[-22:-2].mean()) if len(volumes) >= 22 else float(volumes.iloc[:-1].mean())
    volume_ratio = vol_prev / vol_avg if vol_avg > 0 else 1.0

    high_20d     = float(closes.iloc[-21:-1].max()) if len(closes) >= 21 else float(closes.max())
    near_breakout = price_now >= high_20d * 0.98

    indicators = compute_all(df)
    rsi = indicators.get("rsi", 50.0)

    score = (
        min(max(momentum_5d, 0), 10) * 3.5
        + min(max(volume_ratio - 1, 0), 3) * 10 * 0.25
        + (15 if near_breakout else 0)
        + (10 if indicators.get("macd_bullish_cross") else 0)
        + (10 if indicators.get("bb_pct_b", 0.5) > 0.55 else 0)
        + (10 if (indicators.get("vs_ma20_pct") or 0) > 0
                 and (indicators.get("vs_ma50_pct") or 0) > 0 else 0)
        + max(0, (70 - rsi)) * 0.5
    )

    result = {
        "price":        round(price_now, 2),
        "momentum_5d":  round(momentum_5d, 2),
        "volume_ratio": round(volume_ratio, 2),
        "near_breakout": near_breakout,
        "rsi":          rsi,
        "tech_score":   round(score, 2),
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
) -> list[dict]:
    """
    Download 60d OHLCV per-ticker in parallel, compute technicals, return top_n.
    Uses individual Ticker.history() calls instead of batch download —
    single-ticker requests are fast and a failure only skips that one ticker.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed
    WORKERS = 20   # parallel downloads
    TIMEOUT  = 20  # seconds per individual ticker
    all_results: list[dict] = []
    total = len(tickers)

    print(f"[scanner] downloading {total} tickers individually ({WORKERS} parallel)…")

    def _fetch(symbol: str) -> dict | None:
        try:
            df = yf.Ticker(symbol).history(period="60d", auto_adjust=True)
            if df.empty or len(df) < 5:
                return None
            tech = compute_technicals(df)
            if not tech:
                return None
            if tech["momentum_5d"] > 0 and tech["rsi"] < 75:
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
