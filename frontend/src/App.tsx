import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "./api/client";
import type { Account, Quote, Position, Order, Analysis } from "./api/client";
import { AccountBar } from "./components/AccountBar";
import { StockCard } from "./components/StockCard";
import { PositionsTable } from "./components/PositionsTable";
import { OrdersTable } from "./components/OrdersTable";
import { DailyBrief } from "./components/DailyBrief";
import { BuyCandidates } from "./components/BuyCandidates";
import { HoldingsMonitor } from "./components/HoldingsMonitor";
import { BudgetView } from "./components/BudgetView";
import { BacktestView } from "./components/BacktestView";
import { PortfolioOverview } from "./components/PortfolioOverview";
import "./App.css";

const REFRESH_INTERVAL = 30_000;
const LS_KEY = "watchlist";
const DEFAULT_WATCHLIST = ["AAPL", "NVDA", "MSFT", "TSLA"];

function loadLocalWatchlist(): string[] {
  try {
    const raw = localStorage.getItem(LS_KEY);
    return raw ? JSON.parse(raw) : DEFAULT_WATCHLIST;
  } catch {
    return DEFAULT_WATCHLIST;
  }
}

function saveLocalWatchlist(symbols: string[]) {
  localStorage.setItem(LS_KEY, JSON.stringify(symbols));
}

type Tab = "brief" | "watchlist" | "scan" | "holdings" | "budget" | "backtest" | "positions" | "orders";

export default function App() {
  const [watchlist, setWatchlist] = useState<string[]>(loadLocalWatchlist);
  const [account, setAccount] = useState<Account | null>(null);
  const [quotes, setQuotes] = useState<Quote[]>(() =>
    loadLocalWatchlist().map((symbol) => ({
      symbol, price: 0, prev_close: 0, change_pct: 0, volume: 0, timestamp: "",
    }))
  );
  const [positions, setPositions] = useState<Position[]>([]);
  const [orders, setOrders] = useState<Order[]>([]);
  const [newSymbol, setNewSymbol] = useState("");
  const [backendOnline, setBackendOnline] = useState(true);
  const [tab, setTab] = useState<Tab>("brief");
  const analysisRef = useRef<Record<string, Analysis>>({});

  const refresh = useCallback(async () => {
    // quotes is the health check — only needs yfinance, no Alpaca key
    try {
      const q = await api.getQuotes();
      setBackendOnline(true);
      setQuotes(q.map((quote) => ({
        ...quote,
        analysis: analysisRef.current[quote.symbol] ?? quote.analysis,
      })));
    } catch {
      setBackendOnline(false);
      return;
    }

    // Alpaca-dependent endpoints — fail silently if keys not configured
    const [a, p, o] = await Promise.allSettled([
      api.getAccount(),
      api.getPositions(),
      api.getOrders(),
    ]);
    if (a.status === "fulfilled") setAccount(a.value);
    if (p.status === "fulfilled") setPositions(p.value);
    if (o.status === "fulfilled") setOrders(o.value);
  }, []);

  useEffect(() => {
    refresh();
    const id = setInterval(refresh, REFRESH_INTERVAL);
    return () => clearInterval(id);
  }, [refresh]);

  function handleAnalysisUpdate(a: Analysis) {
    analysisRef.current[a.symbol] = a;
    setQuotes((prev) =>
      prev.map((q) => (q.symbol === a.symbol ? { ...q, analysis: a } : q))
    );
  }

  function handleAddSymbol() {
    const s = newSymbol.trim().toUpperCase();
    if (!s || watchlist.includes(s)) return;
    const next = [...watchlist, s];
    setWatchlist(next);
    saveLocalWatchlist(next);
    setQuotes((prev) => [
      ...prev,
      { symbol: s, price: 0, prev_close: 0, change_pct: 0, volume: 0, timestamp: "" },
    ]);
    setNewSymbol("");
    api.addToWatchlist(s).then(() => refresh()).catch(() => {});
  }

  function handleRemoveSymbol(symbol: string) {
    const next = watchlist.filter((s) => s !== symbol);
    setWatchlist(next);
    saveLocalWatchlist(next);
    setQuotes((prev) => prev.filter((q) => q.symbol !== symbol));
    api.removeFromWatchlist(symbol).catch(() => {});
  }

  const tabs: { id: Tab; label: string }[] = [
    { id: "brief", label: "📋 Daily Brief" },
    { id: "scan", label: "🔍 Buy Candidates" },
    { id: "holdings", label: "📉 Holdings" },
    { id: "budget", label: "💰 Budget" },
    { id: "backtest", label: "📊 Backtest" },
    { id: "watchlist", label: `Watchlist (${quotes.length})` },
    { id: "positions", label: `Positions (${positions.length})` },
    { id: "orders", label: `Orders (${orders.length})` },
  ];

  return (
    <div className="app">
      <header className="app-header">
        <h1>📎 Personal Trading Agent</h1>
        <AccountBar account={account} />
      </header>

      {!backendOnline && (
        <div className="backend-banner">
          ⚠ Backend offline — run <code>python main.py</code> to enable live prices &amp; AI analysis
        </div>
      )}

      <PortfolioOverview backendOnline={backendOnline} />

      <nav className="tab-nav">
        {tabs.map((t) => (
          <button key={t.id} className={`tab ${tab === t.id ? "active" : ""}`} onClick={() => setTab(t.id)}>
            {t.label}
          </button>
        ))}
        <span className="refresh-hint">Auto-refreshes every 30s</span>
      </nav>

      <main className="app-main">
        {tab === "brief" && <DailyBrief backendOnline={backendOnline} />}
        {tab === "scan" && <BuyCandidates backendOnline={backendOnline} />}
        {tab === "holdings" && <HoldingsMonitor backendOnline={backendOnline} />}
        {tab === "budget" && <BudgetView backendOnline={backendOnline} />}
        {tab === "backtest" && <BacktestView backendOnline={backendOnline} />}

        {tab === "watchlist" && (
          <>
            <div className="add-row">
              <input
                className="symbol-input"
                placeholder="Add ticker (e.g. AMZN)"
                value={newSymbol}
                onChange={(e) => setNewSymbol(e.target.value.toUpperCase())}
                onKeyDown={(e) => e.key === "Enter" && handleAddSymbol()}
              />
              <button className="add-btn" onClick={handleAddSymbol}>Add</button>
            </div>
            <div className="cards-grid">
              {quotes.map((q) => (
                <StockCard
                  key={q.symbol}
                  quote={q}
                  onAnalysisUpdate={handleAnalysisUpdate}
                  onRemove={handleRemoveSymbol}
                  backendOnline={backendOnline}
                />
              ))}
            </div>
          </>
        )}

        {tab === "positions" && (
          <section>
            <h2>Open Positions</h2>
            <PositionsTable positions={positions} />
          </section>
        )}

        {tab === "orders" && (
          <section>
            <h2>Recent Orders</h2>
            <OrdersTable orders={orders} />
          </section>
        )}
      </main>
    </div>
  );
}
