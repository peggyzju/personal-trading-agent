const BASE = "/api";

export interface Quote {
  symbol: string;
  price: number;
  prev_close: number;
  change_pct: number;
  volume: number;
  timestamp: string;
  analysis?: Analysis;
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
  price?: number;
  change_pct?: number;
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

async function get<T>(path: string): Promise<T> {
  const r = await fetch(`${BASE}${path}`);
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}

async function post<T>(path: string): Promise<T> {
  const r = await fetch(`${BASE}${path}`, { method: "POST" });
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
  addToWatchlist: (symbol: string) => post<{ symbols: string[] }>(`/watchlist/${symbol}`),
  removeFromWatchlist: (symbol: string) => del<{ symbols: string[] }>(`/watchlist/${symbol}`),
};
