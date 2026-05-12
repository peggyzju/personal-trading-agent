import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "./api/client";
import type { Account, Quote, Position, Order, Analysis } from "./api/client";
import { AccountBar } from "./components/AccountBar";
import { StockCard } from "./components/StockCard";
import { PositionsTable } from "./components/PositionsTable";
import { OrdersTable } from "./components/OrdersTable";
import "./App.css";

const REFRESH_INTERVAL = 30_000;

export default function App() {
  const [account, setAccount] = useState<Account | null>(null);
  const [quotes, setQuotes] = useState<Quote[]>([]);
  const [positions, setPositions] = useState<Position[]>([]);
  const [orders, setOrders] = useState<Order[]>([]);
  const [newSymbol, setNewSymbol] = useState("");
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
      setAccount(a);
      setQuotes(q.map((quote) => ({
        ...quote,
        analysis: analysisRef.current[quote.symbol] ?? quote.analysis,
      })));
      setPositions(p);
      setOrders(o);
    } catch (e) {
      console.error("Refresh failed:", e);
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

  async function handleAddSymbol() {
    const s = newSymbol.trim().toUpperCase();
    if (!s) return;
    await api.addToWatchlist(s);
    setNewSymbol("");
    refresh();
  }

  async function handleRemoveSymbol(symbol: string) {
    await api.removeFromWatchlist(symbol);
    setQuotes((prev) => prev.filter((q) => q.symbol !== symbol));
  }

  return (
    <div className="app">
      <header className="app-header">
        <h1>📎 Personal Trading Agent</h1>
        <AccountBar account={account} />
      </header>

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
