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

## Testing

```bash
python tests/e2e_daily.py          # full test suite (42 checks)
python tests/e2e_daily.py --smoke  # smoke only (env + account + logic)
```
