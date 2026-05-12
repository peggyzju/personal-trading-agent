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
};
