import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "./api/client";
import type { Account, Quote, Position, Order, Analysis } from "./api/client";
import { AccountBar } from "./components/AccountBar";
import { StockCard } from "./components/StockCard";
import { PositionsTable } from "./components/PositionsTable";
import { OrdersTable } from "./components/OrdersTable";
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

export default function App() {
  const [watchlist, setWatchlist] = useState<string[]>(loadLocalWatchlist);
  const [account, setAccount] = useState<Account | null>(null);
  const [quotes, setQuotes] = useState<Quote[]>(() =>
    loadLocalWatchlist().map((symbol) => ({ symbol, price: 0, prev_close: 0, change_pct: 0, volume: 0, timestamp: "" }))
  );
  const [positions, setPositions] = useState<Position[]>([]);
  const [orders, setOrders] = useState<Order[]>([]);
  const [newSymbol, setNewSymbol] = useState("");
  const [backendOnline, setBackendOnline] = useState(true);
  const [tab, setTab] = useState<"watchlist" | "positions" | "orders">("watchlist");
  const analysisRef = useRef<Record<string, Analysis>>({});

  const refresh = useCallback(async () => {
    try {
      const [a, q, p, o] = await Promise.all([
        api.getAccount(),
        api.getQuotes(),
        api.getPositions(),
        api.getOrders(),
      ]);
      setBackendOnline(true);
      setAccount(a);
      setQuotes(q.map((quote) => ({
        ...quote,
        analysis: analysisRef.current[quote.symbol] ?? quote.analysis,
      })));
      setPositions(p);
      setOrders(o);
    } catch {
      setBackendOnline(false);
    }
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

    // sync to backend (best-effort)
    api.addToWatchlist(s).then(() => refresh()).catch(() => {});
  }

  function handleRemoveSymbol(symbol: string) {
    const next = watchlist.filter((s) => s !== symbol);
    setWatchlist(next);
    saveLocalWatchlist(next);
    setQuotes((prev) => prev.filter((q) => q.symbol !== symbol));

    // sync to backend (best-effort)
    api.removeFromWatchlist(symbol).catch(() => {});
  }

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

      <nav className="tab-nav">
        {(["watchlist", "positions", "orders"] as const).map((t) => (
          <button key={t} className={`tab ${tab === t ? "active" : ""}`} onClick={() => setTab(t)}>
            {t === "watchlist" && `Watchlist (${quotes.length})`}
            {t === "positions" && `Positions (${positions.length})`}
            {t === "orders" && `Orders (${orders.length})`}
          </button>
        ))}
        <span className="refresh-hint">Auto-refreshes every 30s</span>
      </nav>

      <main className="app-main">
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
