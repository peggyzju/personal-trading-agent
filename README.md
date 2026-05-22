# Personal Trading Agent

An autonomous AI-powered trading system that scans the market, generates buy/sell signals, and executes trades on Alpaca — with a React dashboard for monitoring and control.

## Agents

Four AI agents work together on a daily pipeline:

| Agent | Role | Schedule |
|-------|------|----------|
| **Maya** | Reads market regime (bull/bear/sideways), sets aggression level and sector bias | 8:00 AM ET |
| **Scout** | 9:00 AM: finds stocks outside the regular universe with unusual volume or momentum (via Finviz), adds up to 30 novel tickers to today's scan universe. 9:31 AM + 12:30 PM: scans S&P 500 + Nasdaq-100 + 65 mid-cap growth stocks + Scout picks, scores candidates with Claude AI | 9:00 AM + 9:31 AM + 12:30 PM ET |
| **Rex** | Reads Scout's scan results → executes buys; reads holdings signals → executes sells | After each scan (buy) · every 30 min (sell) |
| **Vera** | Generates end-of-day strategy review and extracts lessons injected into future scans | 4:15 PM ET |

## Architecture

```
main.py (APScheduler — single scheduler)
  ├── 8:00 AM   Maya  — market context
  ├── 9:00 AM   Scout — dynamic ticker discovery (Finviz)
  ├── 9:31 AM   Scout scan → Rex buy cascade
  ├── 12:30 PM  Scout scan → Rex buy cascade
  ├── every 30 min  Holdings refresh → Rex sell cascade
  ├── every 5 min   Fill sync
  └── 4:15 PM   Vera  — daily review

api/app.py (FastAPI)         frontend/ (React + Vite)
  └── REST endpoints   ←→    └── Portfolio Command Center
```

## Strategy (v3)

### 1. 选股 — Dual-Track Filter + Sector Resonance

Scout runs a two-track screen on ~700 tickers (S&P 500 + Nasdaq-100 + mid-cap layer + dynamic picks):

| Track | Condition | Logic |
|-------|-----------|-------|
| **Track 1 — Momentum Breakout** | RSI 50–75 (85 if sector hot) + `today_bull` + mom5d > 0% + vs_MA20 ≤ 15% | High-RSI stocks breaking out with momentum |
| **Track 2 — Compression Coil** | RSI < 55 + vol_ratio < 0.8× + mom5d > −3% | Low-vol consolidation; bypasses `today_bull` requirement |

**Sector Resonance**: if ≥ 3 stocks in the same sector have `today_bull = True`, that sector's Track 1 RSI ceiling rises from 75 → 85 (catching extended momentum moves in hot sectors like SEMIS).

**Watchlist override**: watchlist symbols with `today_bull = True` bypass both tracks; otherwise they fall back to Track 2 conditions only.

### 2. 买入 — Rex Buy Logic

| Gate | Rule |
|------|------|
| **Market hours** | Only operates 9:25–16:05 ET Mon–Fri |
| **Scan freshness** | Scan must be from today (ET date) — yesterday's signals are ignored |
| **Signal staleness** | Price must be within ±1.5% of scan price — larger drift means the signal is stale |
| **Earnings filter** | Skip if earnings are today or tomorrow (未公布). Post-earnings momentum (已公布) is allowed |
| **Stop loss** | `max(MA20 × 0.99, entry − 2×ATR)`, clamped to −3% (tightest) / −8% (widest) |
| **Position sizing** | ATR-based (risk_pct of portfolio per trade) |

Orders are bracket limit orders (GTC stop-loss placed on Alpaca at time of entry).

### 3. 卖出 — Rex Sell Logic

| Mechanism | Rule |
|-----------|------|
| **Hard stop** | −3% from entry → bracket GTC order on Alpaca executes automatically (no polling needed) |
| **Trailing stop** | Activates at **+6%** gain. Tracks high watermark; triggers SELL if price drops **5%** from peak |
| **AI soft exit** | Claude evaluates each position every 30 min via holdings monitor |
| **Hold cooldown** | 2 consecutive HOLD signals required to cancel a pending sell (prevents over-trading) |
| **REDUCE escalation** | 2 consecutive REDUCE signals → escalated to SELL |

## Stack

- **AI**: Claude (Anthropic) — signal scoring, sell analysis, strategy review
- **Broker**: Alpaca Paper / Live trading API
- **Market data**: yfinance, Finviz
- **Backend**: FastAPI + APScheduler
- **Frontend**: React + TypeScript + Vite

## Setup

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Configure credentials
cp .env.example .env
# Fill in: ANTHROPIC_API_KEY, ALPACA_API_KEY, ALPACA_SECRET_KEY

# 3. Start backend
python main.py

# 4. Start frontend (separate terminal)
cd frontend && npm install && npm run dev
```

Open `http://localhost:5173` for the dashboard, `http://localhost:8000/docs` for the API.

## Scan Universe

| Source | Coverage |
|--------|----------|
| S&P 500 | ~500 large-cap US stocks |
| Nasdaq-100 | ~100 tech/growth stocks |
| Layer2 | 65 hand-picked mid-cap growth stocks (SaaS, biotech, EV, quantum, Chinese ADRs, etc.) |
| Scout dynamic | Up to 30 novel tickers discovered each morning via Finviz |

## Configuration

`data/` stores runtime state — all auto-generated on first run:

| File | Purpose |
|------|---------|
| `scan_cache.json` | Latest Scout scan results with AI scores |
| `dynamic_tickers.json` | Today's Scout-discovered tickers (TTL: 1 trading day) |
| `strategy_notes.json` | Lessons extracted by Vera, injected into future scans |
| `auto_approve.json` | Autonomous execution config (enabled, confidence threshold) |
| `market_context.json` | Current regime, aggression level, sector bias |
| `trailing_stops.json` | Live high-watermarks and trailing stop prices per position |
| `versions.json` | Strategy version history for backtest comparison (v_prev vs v_current) |

## Strategy Versions

| Version | Description |
|---------|-------------|
| v1 | 单轨选股 (RSI<60 + MA20≤8%) + 固定止盈 8% |
| v2 | 单轨选股 + 追踪止盈 (+12% 激活, 8% 回落) |
| v3 *(current)* | 双轨选股 + 板块共振 + 追踪止盈 (+6% 激活, 5% 回落) + 2×ATR 止损 |

## Testing

```bash
python tests/e2e_daily.py          # full test suite
python tests/e2e_daily.py --smoke  # smoke only (env + account + logic)
```
