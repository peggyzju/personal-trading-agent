import { useCallback, useEffect, useState } from "react";
import { api } from "./api/client";
import type { Account, Position, Order } from "./api/client";
import { AccountBar } from "./components/AccountBar";
import { PositionsTable } from "./components/PositionsTable";
import { OrdersTable } from "./components/OrdersTable";
import { DailyBrief } from "./components/DailyBrief";
import { PortfolioCommandCenter } from "./components/PortfolioCommandCenter";
import { BacktestView } from "./components/BacktestView";
import { StockResearch } from "./components/StockResearch";
import { StrategyReviewPanel } from "./components/StrategyReview";
import "./App.css";

const REFRESH_INTERVAL = 30_000;

type Tab = "portfolio" | "research" | "review" | "brief" | "backtest" | "positions" | "orders";

export default function App() {
  const [account, setAccount] = useState<Account | null>(null);
  const [positions, setPositions] = useState<Position[]>([]);
  const [orders, setOrders] = useState<Order[]>([]);
  const [backendOnline, setBackendOnline] = useState(true);
  const [tab, setTab] = useState<Tab>("portfolio");

  const refresh = useCallback(async () => {
    // All endpoints in parallel — health check and Alpaca data are independent
    const [q, a, p, o] = await Promise.allSettled([
      api.getQuotes(),
      api.getAccount(),
      api.getPositions(),
      api.getOrders(),
    ]);
    setBackendOnline(q.status === "fulfilled");
    if (a.status === "fulfilled") setAccount(a.value);
    if (p.status === "fulfilled") setPositions(p.value);
    if (o.status === "fulfilled") setOrders(o.value);
  }, []);

  useEffect(() => {
    refresh();
    const id = setInterval(refresh, REFRESH_INTERVAL);
    return () => clearInterval(id);
  }, [refresh]);

  const tabs: { id: Tab; label: string }[] = [
    { id: "portfolio",  label: "📊 组合" },
    { id: "research",   label: "🔬 选股" },
    { id: "review",     label: "📈 策略复盘" },
    { id: "brief",      label: "📋 每日简报" },
    { id: "backtest",   label: "🧪 回测" },
    { id: "positions",  label: `📂 仓位 (${positions.length})` },
    { id: "orders",     label: `📑 订单 (${orders.length})` },
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

      <nav className="tab-nav">
        {tabs.map((t) => (
          <button
            key={t.id}
            className={`tab ${tab === t.id ? "active" : ""}`}
            onClick={() => setTab(t.id)}
          >
            {t.label}
          </button>
        ))}
        <span className="refresh-hint">每 30 秒自动刷新</span>
      </nav>

      <main className="app-main">
        {tab === "portfolio"  && <PortfolioCommandCenter backendOnline={backendOnline} />}
        {tab === "research"   && <StockResearch backendOnline={backendOnline} />}
        {tab === "review"     && <StrategyReviewPanel backendOnline={backendOnline} />}
        {tab === "brief"      && <DailyBrief backendOnline={backendOnline} />}
        {tab === "backtest"   && <BacktestView backendOnline={backendOnline} />}

        {tab === "positions" && (
          <section>
            <h2>仓位详情</h2>
            <PositionsTable positions={positions} onRefresh={refresh} />
          </section>
        )}

        {tab === "orders" && (
          <section>
            <h2>订单记录</h2>
            <OrdersTable orders={orders} onRefresh={refresh} />
          </section>
        )}
      </main>
    </div>
  );
}
