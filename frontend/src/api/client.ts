const BASE = "/api";

export interface Quote {
  symbol: string;
  price: number;
  prev_close: number;
  change_pct: number;
  volume: number;
  timestamp: string;
  analysis?: Analysis;
  news_sentiment?: "BULLISH" | "BEARISH" | "NEUTRAL";
  error?: string;
}

export interface Analysis {
  symbol: string;
  signal: "BUY" | "SELL" | "HOLD";
  confidence: number;
  target_price: number;
  stop_loss: number;
  reasoning: string;
  key_risks: string[];
  technical_notes?: string;
  catalyst?: string;
  price?: number;
  change_pct?: number;
}

export interface NewsItem {
  title: string;
  summary: string;
  published: string;
  url: string;
  source: string;
  relevance?: "HIGH" | "MEDIUM" | "LOW";
  sentiment?: "BULLISH" | "BEARISH" | "NEUTRAL";
  impact?: "IMMEDIATE" | "SHORT_TERM" | "LONG_TERM";
  reason?: string;
}

export interface NewsSentiment {
  symbol: string;
  overall: "BULLISH" | "BEARISH" | "NEUTRAL";
  key_insight: string;
  watch_for: string;
  items: NewsItem[];
}

export interface Position {
  symbol: string;
  qty: number;
  avg_entry_price: number;
  current_price: number;
  market_value: number;
  unrealized_pl: number;
  unrealized_plpc: number;
  side: string;
}

export interface Account {
  equity: number;
  buying_power: number;
  cash: number;
  portfolio_value: number;
  daytrade_count: number;
  status: string;
}

export interface Order {
  id: string;
  symbol: string;
  side: string;
  qty: number;
  filled_qty: number;
  filled_avg_price: number | null;
  status: string;
  created_at: string;
  type: string;
}

export interface ScanCandidate {
  symbol: string;
  price: number;
  signal: "STRONG_BUY" | "BUY" | "WATCH";
  ai_score: number;
  reason: string;
  entry_note?: string;
  stop_loss?: number;
  target_price?: number;
  timeframe?: string;
  momentum_5d?: number;
  volume_ratio?: number;
  rsi?: number;
  near_breakout?: boolean;
}

export interface ScanResult {
  status: "not_run" | "running" | "done" | "error";
  candidates: ScanCandidate[];
  scanned_at?: string;
  total_screened?: number;
  tech_passed?: number;
  error?: string;
}

export interface HoldingPosition {
  symbol: string;
  qty: number;
  avg_entry_price: number;
  current_price: number;
  market_value: number;
  unrealized_pl: number;
  unrealized_plpc: number;
  side: string;
  sell_signal?: "SELL" | "REDUCE" | "HOLD" | "ADD";
  urgency?: "HIGH" | "MEDIUM" | "LOW";
  reason?: string;
  suggested_action?: string;
}

export interface HoldingsResult {
  positions: HoldingPosition[];
  analyzed: boolean;
}

export interface BudgetHolding {
  symbol: string;
  market_value: number;
  pct: number;
  unrealized_pl: number;
  unrealized_plpc: number;
}

export interface BudgetBuy {
  symbol: string;
  signal: string;
  ai_score: number;
  price: number;
  stop_loss: number;
  target_price: number;
  reason: string;
  shares: number;
  cost: number;
  max_loss: number;
  portfolio_pct: number;
  risk_pct_actual: number;
}

export interface BudgetAllocation {
  portfolio_value: number;
  cash: number;
  invested: number;
  cash_pct: number;
  invested_pct: number;
  slots_remaining: number;
  risk_per_trade_pct: number;
  max_position_pct: number;
  holdings: BudgetHolding[];
  suggested_buys: BudgetBuy[];
  total_suggested_cost: number;
}

export interface PortfolioDay {
  date: string;
  equity: number;
  daily_pl: number;
  daily_return_pct: number;
}

export interface PortfolioHistory {
  current_equity: number;
  base_value: number;
  total_pl: number;
  total_return_pct: number;
  days: PortfolioDay[];
  source: "alpaca" | "demo";
}

export interface BacktestTrade {
  symbol: string;
  entry_date: string;
  exit_date: string;
  entry_price: number;
  exit_price: number;
  pnl_pct: number;
  exit_reason: "stop_loss" | "target_hit" | "time_exit";
  days_held: number;
  atr_at_entry: number;
}

export interface BacktestResult {
  status: "not_run" | "running" | "done" | "error";
  total_trades?: number;
  win_rate?: number;
  avg_win_pct?: number;
  avg_loss_pct?: number;
  profit_factor?: number;
  total_return_pct?: number;
  spy_return_pct?: number;
  alpha_pct?: number;
  max_drawdown_pct?: number;
  sharpe_ratio?: number;
  exit_breakdown?: Record<string, number>;
  equity_curve?: number[];
  trades?: BacktestTrade[];
  symbols?: string[];
  params?: { hold_days: number; target_pct: number; period: string };
  error?: string;
}

export interface DailyBrief {
  headline: string;
  market_mood: "RISK_ON" | "RISK_OFF" | "MIXED";
  top_movers: { symbol: string; change_pct: number; reason: string }[];
  key_events: { event: string; impact: "BULLISH" | "BEARISH" | "NEUTRAL"; detail: string }[];
  trading_opportunities: { symbol: string; action: "BUY" | "SELL" | "WATCH"; rationale: string }[];
  risks_to_watch: string[];
  sentiment_summary: string;
  generated_at: string;
}

async function get<T>(path: string): Promise<T> {
  const r = await fetch(`${BASE}${path}`);
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}

async function post<T>(path: string, body?: unknown): Promise<T> {
  const r = await fetch(`${BASE}${path}`, {
    method: "POST",
    ...(body ? { headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) } : {}),
  });
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}

async function del<T>(path: string): Promise<T> {
  const r = await fetch(`${BASE}${path}`, { method: "DELETE" });
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}

export const api = {
  getQuotes: () => get<Quote[]>("/quotes"),
  getAccount: () => get<Account>("/account"),
  getPositions: () => get<Position[]>("/positions"),
  getOrders: () => get<Order[]>("/orders"),
  analyze: (symbol: string) => post<Analysis>(`/analyze/${symbol}`),
  getNews: (symbol: string) => get<{ symbol: string; items: NewsItem[] }>(`/news/${symbol}`),
  analyzeNewsSentiment: (symbol: string) => post<NewsSentiment>(`/news/${symbol}/sentiment`),
  getMovers: () => get<{ gainers: Quote[]; losers: Quote[]; all: Quote[] }>("/movers"),
  getBrief: () => get<DailyBrief>("/brief"),
  generateBrief: () => post<DailyBrief>("/brief"),
  addToWatchlist: (symbol: string) => post<{ symbols: string[] }>(`/watchlist/${symbol}`),
  removeFromWatchlist: (symbol: string) => del<{ symbols: string[] }>(`/watchlist/${symbol}`),
  getPortfolioHistory: () => get<PortfolioHistory>("/portfolio/history"),
  getBacktest: () => get<BacktestResult>("/backtest"),
  triggerBacktest: (params: { hold_days?: number; target_pct?: number; period?: string }) => {
    const q = new URLSearchParams(
      Object.entries(params).filter(([, v]) => v !== undefined).map(([k, v]) => [k, String(v)])
    ).toString();
    return post<{ status: string }>(`/backtest${q ? "?" + q : ""}`);
  },
  getScan: () => get<ScanResult>("/scan/sp500"),
  triggerScan: () => post<{ status: string }>("/scan/sp500"),
  getHoldings: () => get<HoldingsResult>("/scan/holdings"),
  refreshHoldings: () => post<{ status: string }>("/scan/holdings"),
  getBudget: () => get<BudgetAllocation>("/budget"),
};
