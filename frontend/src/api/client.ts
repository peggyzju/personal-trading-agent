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

export interface TradeRequest {
  symbol: string;
  side: "buy" | "sell";
  qty?: number;
  notional?: number;
  order_type?: "market" | "limit" | "stop" | "stop_limit";
  limit_price?: number;
  stop_price?: number;
}

export interface TradeResult {
  id: string;
  symbol: string;
  side: string;
  qty: string | null;
  notional: string | null;
  type: string;
  status: string;
  created_at: string;
}

export interface ScanCandidate {
  symbol: string;
  price: number;
  signal: "STRONG_BUY" | "BUY" | "HOLD" | "SELL" | "WATCH";
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
  owned?: boolean;
  universe?: "sp500" | "nasdaq100" | "layer2" | "other";
  // Fundamentals (from yfinance enrichment)
  company_name?: string;
  sector?: string;
  industry?: string;
  pe_ratio?: number | null;
  market_cap?: number | null;
  beta?: number | null;
  week52_high?: number | null;
  week52_low?: number | null;
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

export interface StrategyIterationOp {
  title: string;
  description: string;
  priority: "HIGH" | "MEDIUM" | "LOW";
  expected_impact: string;
}

export interface DebateResult {
  pro: string;
  con: string;
  synthesis: string;
  recommendation: "ADOPT" | "HOLD" | "REJECT";
  confidence: number;
}

export interface ParamChange {
  name: "risk_pct" | "max_position_pct" | "min_ai_score" | "stop_loss_pct";
  label: string;
  current: number;
  proposed: number;
  unit: string;
  display_current: string;
  display_proposed: string;
}

export interface ParamExtractResult {
  mappable: boolean;
  note: string;
  params: ParamChange[];
}

export interface StrategyOverrides {
  risk_pct: number;
  max_position_pct: number;
  min_ai_score: number | null;
  stop_loss_pct: number;
  updated_at: string | null;
  reason: string | null;
}

export interface StrategyReview {
  date: string;
  generated_at: string;
  one_line_summary: string;
  market_context: string;
  core_strategy_assessment: string;
  what_worked: string[];
  what_didnt: string[];
  monthly_progress_note: string;
  iteration_opportunities: StrategyIterationOp[];
  tomorrow_focus: string;
  performance: {
    daily_pl: number;
    daily_return_pct: number;
    monthly_return_pct: number;
    target_monthly_pct: number;
    target_gap: number;
    current_equity: number;
  };
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

export interface PendingTrade {
  id: string;
  symbol: string;
  side: "buy" | "sell";
  notional: number | null;
  qty: number | null;
  signal: string;
  confidence: number;
  reason: string;
  source: "scanner" | "watchlist" | "holdings";
  stop_loss: number | null;
  target_price: number | null;
  price: number | null;
  price_drift_pct?: number | null;
  rsi?: number | null;
  momentum_5d?: number | null;
  volume_ratio?: number | null;
  near_breakout?: boolean | null;
  universe?: string | null;
  status: "pending" | "approved" | "rejected" | "executed" | "expired" | "error";
  created_at: string;
  expires_at: string;
  executed_order_id: string | null;
  error: string | null;
}

export interface GoalProgress {
  start_equity: number;
  current_equity: number;
  target_equity_low: number;
  target_equity_mid: number;
  current_return_pct: number;
  target_return_pct: number;
  days_elapsed: number;
  days_remaining: number;
  daily_return_needed: number;
  on_track: boolean;
  gap_pct: number;
  aggression: "conservative" | "normal" | "aggressive";
  target_pct_low: number;
  target_pct_high: number;
  total_days: number;
  start_date: string;
}

export interface AgentState {
  trades: PendingTrade[];
  log: { run_at: string; signals_found: number; trades_queued: number; sources: string[]; status: string; regime?: string; regime_reason?: string; error?: string }[];
}

export interface MarketRegime {
  regime: "BULL" | "NEUTRAL" | "CAUTION" | "BEAR";
  spy_price: number;
  spy_change_pct: number;
  spy_vs_ma20: number;
  spy_vs_ma50: number;
  min_ai_score: number;
  size_factor: number;
  block_buys: boolean;
  reason: string;
  fetched_at: number;
}

export interface CircuitBreaker {
  triggered: boolean;
  reason: string;
  triggered_at: string | null;
  daily_loss_pct: number;
  date: string;
}

async function del<T>(path: string): Promise<T> {
  const r = await fetch(`${BASE}${path}`, { method: "DELETE" });
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}

export const api = {
  getQuotes: () => get<Quote[]>("/quotes"),
  getQuoteSingle: (symbol: string) => get<Quote>(`/quotes/${symbol}`),
  getAccount: () => get<Account>("/account"),
  getPositions: () => get<Position[]>("/positions"),
  getOrders: () => get<Order[]>("/orders"),
  analyze: (symbol: string) => post<Analysis>(`/analyze/${symbol}`),
  getNews: (symbol: string) => get<{ symbol: string; items: NewsItem[] }>(`/news/${symbol}`),
  analyzeNewsSentiment: (symbol: string) => post<NewsSentiment>(`/news/${symbol}/sentiment`),
  getMovers: () => get<{ gainers: Quote[]; losers: Quote[]; all: Quote[] }>("/movers"),
  getBrief: () => get<DailyBrief>("/brief"),
  generateBrief: () => post<DailyBrief>("/brief"),
  getWatchlist: () => get<{ symbols: string[] }>("/watchlist"),
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
  enrichScan: () => post<ScanResult & { status: string }>("/scan/enrich"),
  getHoldings: () => get<HoldingsResult>("/scan/holdings"),
  refreshHoldings: () => post<{ status: string }>("/scan/holdings"),
  getBudget: () => get<BudgetAllocation>("/budget"),
  placeTrade: (req: TradeRequest) => post<TradeResult>("/trade", req),
  closePosition: (symbol: string) => del<{ status: string; order_id: string }>(`/positions/${symbol}`),
  cancelOrder: (orderId: string) => del<{ status: string }>(`/orders/${orderId}`),
  getAgentState: () => get<AgentState>("/agent/pending"),
  runAgent: () => post<{ status: string }>("/agent/run"),
  approveTrade: (id: string) => post<PendingTrade>(`/agent/pending/${id}/approve`),
  rejectTrade: (id: string) => post<PendingTrade>(`/agent/pending/${id}/reject`),
  getAutoApprove: () => get<{ enabled: boolean; threshold: number }>("/agent/auto-approve"),
  setAutoApprove: (enabled: boolean, threshold: number) =>
    post<{ enabled: boolean; threshold: number }>("/agent/auto-approve", { enabled, threshold }),
  getMarketRegime: () => get<MarketRegime>("/market/regime"),
  getCircuitBreaker: () => get<CircuitBreaker>("/circuit-breaker"),
  resetCircuitBreaker: () => post<CircuitBreaker>("/circuit-breaker/reset"),
  getStrategyReview: () => get<StrategyReview | { status: string }>("/strategy/review"),
  getStrategyReviews: () => get<StrategyReview[]>("/strategy/reviews"),
  generateStrategyReview: () => post<{ status: string }>("/strategy/review"),
  debateIteration: (op: StrategyIterationOp) => post<DebateResult>("/strategy/debate", op),
  extractParams: (op: StrategyIterationOp) => post<ParamExtractResult>("/strategy/param-extract", op),
  getOverrides: () => get<StrategyOverrides>("/strategy/overrides"),
  saveOverrides: (patch: Partial<StrategyOverrides> & { reason?: string }) =>
    post<StrategyOverrides>("/strategy/overrides", patch),
  getPipelineStatus: () => get<PipelineStatus>("/pipeline/status"),
  getGoalProgress: () => get<GoalProgress>("/goal/progress"),
  getScanNasdaq: () => get<ScanResult>("/scan/nasdaq"),
};

export interface PipelineStage {
  status: "not_run" | "running" | "done" | "error";
  age?: string | null;
  generated_at?: string | null;
  scanned_at?: string | null;
  last_run_at?: string | null;
}
export interface PipelineStatus {
  market_context: PipelineStage & { regime?: string; aggression?: string; min_ai_score?: number };
  scan: PipelineStage & { total_screened?: number; candidates?: number };
  agent: PipelineStage & { signals_found?: number; trades_queued?: number; pending_approval?: number };
  review: PipelineStage & { one_line?: string };
}
