# Personal Trading Agent

An autonomous AI-assisted trading system that scans the US market, ranks stocks by momentum, and executes trades on Alpaca — with a React dashboard for monitoring and control. Currently runs in **paper trading** mode.

> **Strategy v12 (2026-07-07):** keeps v10 completed-daily structure + intraday confirmation, v11 software-over-hardware soft rotation overlay, and v9 BE5/trail5 exits. Rex adds EF5 early-failure exits: D1-D2 positions with floating loss at or below -5% are recycled before they hard-stop. Entry/exit remain rule-based; AI is advisory only — landmine veto (→ manual review), post-earnings judgment, and end-of-day review.

## Agents

Four AI agents coordinated by a single APScheduler (America/New_York):

| Agent | Role | Schedule (ET) |
|-------|------|---------------|
| **Maya** | Reads market regime (BULL/NEUTRAL/CAUTION/BEAR/CRASH) → sets position cap, size factor, buy block | 8:00 AM |
| **Scout** | Pre-market dynamic discovery (Finviz) + full-universe scan + mechanical momentum ranking (+ AI landmine veto) | 8:45 (discovery) · 9:31 / 11:00 / 12:30 / 14:30 (scan) |
| **Rex** | Reads scan candidates → executes buys (momentum order); monitors holdings → executes mechanical sells | After each scan (buy) · every 30 min (sell) |
| **Vera** | End-of-day strategy review, extracts lessons | Manual trigger (`POST /api/strategy/review`) |

## Architecture

```
main.py (APScheduler — single source of truth, US/Eastern)
  ├── 8:00 AM            Maya  — market regime
  ├── 8:45 AM            Scout — dynamic discovery (Finviz)
  ├── 9:31/11:00/12:30/14:30  Scout scan → Rex buy cascade
  ├── every 30 min       Holdings refresh → Rex sell cascade
  ├── every 15 min       Earnings radar (post-report AI judgment)
  └── heartbeat + independent watchdog LaunchAgent (auto-restart on freeze)

api/app.py (FastAPI :8000)        frontend/ (React + Vite)
  └── REST endpoints   ←→         └── Portfolio Command Center
```

## Strategy (v12 — daily structure + intraday confirmation + rotation overlay + EF5)

### 1. Selection (Scout) — completed structure, intraday confirmation
A stock passes only if the completed-daily structure and current intraday confirmation both hold (no dual-track, no AI score gate, no sector boost):
- completed daily structure: previous close **> MA50**, **MA50 rising** (5-day slope > 0), previous **3-month momentum > 0**
- intraday confirmation: current price **> previous MA50**, current **RSI 50–80**, current **3-month momentum > 0**, current **vs previous MA20 ≤ 15%**

Passing stocks are ranked by **previous completed-day 3-month momentum**; the candidate price is updated to the current intraday price for sizing/stops. Watchlist symbols go through the same gate (no special treatment).

When the AI-chain software group clearly leads hardware, Scout enables a **soft rotation overlay** before top-N truncation:
- trigger: software 3-day median return minus hardware 3-day median return **> 3%**, hardware 1-day median return **< -2%**, and software MA20 breadth **>** hardware MA20 breadth
- order: **software → other → hardware**, while each bucket still sorts by previous completed-day 3-month momentum
- hardware is not banned, but must have **RSI ≥ 52** and current price **≥ MA20**
- caps: top10 max **6 software / 3 hardware**; top25 max **12 software / 8 hardware**

### 2. Buy (Rex) — momentum order, auto by default
- Candidates in momentum order → entry gate: market hours · fresh signal · price drift ≤ 1.5% · no earnings today/tomorrow
- **Fixed −8% stop** (`price × 0.92`)
- Position sizing: risk 2% / trade, ≤ 8% per position, scaled by regime `size_factor`
- **Auto-executes by default** (no AI score gate). The only thing routing a buy to **manual review** is an **AI landmine veto** (`veto=true` — a concrete, named risk).
- Regime gate: BEAR → all buys blocked; CAUTION → reduced size + smaller position cap.

### 3. Sell (Rex) — purely mechanical (no AI)

| Mechanism | Rule |
|-----------|------|
| **Hard stop** | −8% from entry → Alpaca bracket GTC order (server-side, ms-latency) + holdings-monitor fallback |
| **EF5 early-failure stop** | D1-D2 positions with floating loss **≤ −5%** → exit; D0 is ignored |
| **Breakeven stop** | Activates at **+5%** high watermark; triggers SELL if price returns to entry/average cost |
| **Trailing stop** | Activates at **+6%** high watermark; triggers SELL if price falls **5%** from the high watermark |
| **MA20 break** | **2 consecutive daily closes below MA20** → trend over, exit (single-bar dips are held — avoids whipsaw) |

## Stack

- **AI**: Claude (Anthropic) — landmine veto, post-earnings judgment, strategy review (not buy/sell decisions)
- **Broker**: Alpaca Paper / Live trading API
- **Market data**: Alpaca (bars, `feed=iex`) · Finnhub (company news, earnings calendar)
- **Backend**: FastAPI + APScheduler
- **Frontend**: React + TypeScript + Vite

## Setup

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Configure credentials
cp .env.example .env
# Fill in: ANTHROPIC_API_KEY, ALPACA_API_KEY, ALPACA_SECRET_KEY, FINNHUB_API_KEY

# 3. Start backend (or via LaunchAgent — see notes)
python main.py

# 4. Start frontend (separate terminal)
cd frontend && npm install && npm run dev
```

Dashboard: `http://localhost:5173` (dev) — production build is served by the backend at `http://localhost:8000`. API docs: `http://localhost:8000/docs`.

## Scan Universe

| Source | Coverage |
|--------|----------|
| S&P 500 | ~500 large-cap US stocks |
| Nasdaq-100 | ~100 tech/growth stocks |
| Layer 2 | hand-picked mid-cap growth stocks |
| Scout dynamic | novel tickers discovered each morning via Finviz |

## Runtime data (`data/` — auto-generated)

| File | Purpose |
|------|---------|
| `scan_cache.json` | Latest Scout scan (momentum-ranked candidates + AI veto) |
| `dynamic_tickers.json` | Today's Scout-discovered tickers (TTL: 1 trading day) |
| `auto_approve.json` | Autonomous execution config (pure enabled switch; fail-closed if missing) |
| `market_context.json` | Current regime, size factor, position cap |
| `trailing_stops.json` | Per-position high-watermarks + trailing stop prices |
| `earnings_calendar.json` / `earnings_analysis.json` | Earnings radar (upcoming + post-report AI judgment) |
| `scheduler_heartbeat.json` | Scheduler liveness (watched by the watchdog) |
| `versions.json` | Strategy version history (v1–v12) |

## Testing & self-check

```bash
python tests/e2e_daily.py             # full suite
python tests/e2e_daily.py --smoke     # smoke only (env + account + core logic)
PYTHONPATH=. python scripts/mock_pipeline.py   # 全链路 mock: Maya→Scout→Rex(只读,不下单)
```

Backtest scripts (offline, survivorship-biased universe — relative comparison is reliable):
`scripts/v8_backtest.py`, `scripts/v8_robustness.py`, `scripts/v8_ma20_exit_test.py`, `scripts/v8_ma20_volume_test.py`, `scripts/v8_exit_ab_backtest.py`.

> Strategy + operating guide: `CLAUDE.md` (single source of truth).
