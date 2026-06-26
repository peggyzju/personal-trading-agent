import { useCallback, useEffect, useState } from "react";
import { api } from "./api/client";
import type { Account, Position, Order, MarketRegime, CircuitBreaker } from "./api/client";
import { PortfolioCommandCenter } from "./components/PortfolioCommandCenter";
import { SignalsView } from "./components/SignalsView";
import { StrategyReviewPanel } from "./components/StrategyReview";
import { ToolsView } from "./components/ToolsView";
import "./App.css";

const REFRESH_INTERVAL = 30_000;

type Tab = "portfolio" | "signals" | "review" | "tools";

const REGIME_LABEL: Record<string, string> = {
  BULL: "牛市", NEUTRAL: "平稳", CAUTION: "谨慎", BEAR: "熊市",
};
const REGIME_COLOR: Record<string, string> = {
  BULL: "#22c55e", NEUTRAL: "#818cf8", CAUTION: "#f59e0b", BEAR: "#ef4444",
};

export default function App() {
  const [account, setAccount] = useState<Account | null>(null);
  const [positions, setPositions] = useState<Position[]>([]);
  const [orders, setOrders] = useState<Order[]>([]);
  const [backendOnline, setBackendOnline] = useState(true);
  const [tab, setTab] = useState<Tab>("portfolio");
  const [autoApprove, setAutoApprove] = useState<{ enabled: boolean; threshold: number }>({ enabled: true, threshold: 0.0 });
  const [pendingCount, setPendingCount] = useState(0);
  const [regime, setRegime] = useState<MarketRegime | null>(null);
  const [breaker, setBreaker] = useState<CircuitBreaker | null>(null);

  const refresh = useCallback(async () => {
    // 后端在线探针用 getAccount（快、稳）。原来用 getQuotes 当探针——它串行拉
    // 整个 watchlist 的 yfinance 报价、常卡死，导致 header equity 永远「加载中」。
    const [a, p, o] = await Promise.allSettled([
      api.getAccount(),
      api.getPositions(),
      api.getOrders(),
    ]);
    setBackendOnline(a.status === "fulfilled");
    if (a.status === "fulfilled") setAccount(a.value);
    if (p.status === "fulfilled") setPositions(p.value);
    if (o.status === "fulfilled") setOrders(o.value);
  }, []);

  const refreshHeader = useCallback(async () => {
    if (!backendOnline) return;
    const [aa, agent, reg, brk] = await Promise.allSettled([
      api.getAutoApprove(),
      api.getAgentState(),
      api.getMarketRegime(),
      api.getCircuitBreaker(),
    ]);
    if (aa.status    === "fulfilled") setAutoApprove(aa.value);
    if (agent.status === "fulfilled")
      setPendingCount(agent.value.trades.filter(t => t.status === "pending").length);
    if (reg.status   === "fulfilled") setRegime(reg.value);
    if (brk.status   === "fulfilled") setBreaker(brk.value);
  }, [backendOnline]);

  useEffect(() => {
    refresh();
    const id = setInterval(refresh, REFRESH_INTERVAL);
    return () => clearInterval(id);
  }, [refresh]);

  useEffect(() => {
    refreshHeader();
    const id = setInterval(refreshHeader, 20_000);
    return () => clearInterval(id);
  }, [refreshHeader]);

  async function toggleAutoApprove() {
    const next = { ...autoApprove, enabled: !autoApprove.enabled };
    setAutoApprove(next);
    try {
      const cfg = await api.setAutoApprove(next.enabled, next.threshold);
      setAutoApprove(cfg);
    } catch {
      setAutoApprove(autoApprove);
    }
  }

  async function resetBreaker() {
    try {
      const b = await api.resetCircuitBreaker();
      setBreaker(b);
    } catch { /* ignore */ }
  }

  const tabs: { id: Tab; label: string; badge?: number }[] = [
    { id: "portfolio", label: "📊 今日", badge: pendingCount || undefined },
    { id: "signals",   label: "📡 信号" },
    { id: "review",    label: "📈 复盘" },
    { id: "tools",     label: "🔧 工具" },
  ];

  return (
    <div className="app">
      <header className="app-header">
        <span className="hdr-brand">⚡ Trading Agent</span>
        <div className="hdr-sep" />
        {account ? (
          <div className="hdr-equity">
            <span className="hdr-eq-val">${account.equity.toLocaleString("en-US", { maximumFractionDigits: 0 })}</span>
            <span className="hdr-eq-sub">组合权益</span>
          </div>
        ) : (
          <span className="hdr-eq-sub">加载中…</span>
        )}
        <div className="hdr-sep" />

        {/* Compact regime + breaker chips */}
        {regime && (
          <span
            className="hdr-regime-chip"
            style={{ color: REGIME_COLOR[regime.regime] ?? "#818cf8", borderColor: (REGIME_COLOR[regime.regime] ?? "#818cf8") + "40" }}
            title={regime.reason}
          >
            <span className="hdr-regime-name">
              {regime.regime} <span className="hdr-regime-label">{REGIME_LABEL[regime.regime] ?? regime.regime}</span>
            </span>
            <span className="hdr-regime-sub">
              SPY {regime.spy_change_pct >= 0 ? "+" : ""}{regime.spy_change_pct.toFixed(1)}%
              {regime.block_buys ? " · 暂停买入" : ` · ${Math.round(regime.size_factor * 100)}%仓位`}
            </span>
          </span>
        )}
        {breaker && (
          <span
            className={`hdr-breaker-chip${breaker.triggered ? " triggered" : ""}`}
            title={breaker.triggered ? breaker.reason : `今日亏损 ${breaker.daily_loss_pct.toFixed(2)}%，熔断未触发`}
          >
            <span>{breaker.triggered ? "🚨" : "🛡"}</span>
            <span className="hdr-breaker-text">
              {breaker.triggered ? "熔断触发" : "熔断正常"}
              <span className="hdr-regime-sub">
                {" "}今日 {breaker.daily_loss_pct >= 0 ? "+" : ""}{breaker.daily_loss_pct.toFixed(2)}%
              </span>
            </span>
            {breaker.triggered && (
              <button className="hdr-breaker-reset" onClick={resetBreaker}>重置</button>
            )}
          </span>
        )}

        {/* 历史/持仓胜率统计已移至「复盘」收益面板 */}

        <div className="hdr-spacer" />
        <div className="hdr-sep" />
        <button
          className={`hdr-approve-btn${autoApprove.enabled ? " auto" : " manual"}`}
          onClick={toggleAutoApprove}
          title={autoApprove.enabled
            ? "自主模式：Agent 自动执行所有交易，点击切换为人工审批"
            : "人工审批模式：每笔交易需手动确认，点击开启自主模式"}
        >
          <span className="hdr-approve-label">{autoApprove.enabled ? "自主模式" : "人工审批"}</span>
          <div className={`hdr-toggle${autoApprove.enabled ? " on" : ""}`}>
            <div className="hdr-toggle-dot" />
          </div>
        </button>
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
            {t.badge ? <span className="tab-badge">{t.badge}</span> : null}
          </button>
        ))}
        <span className="refresh-hint">每 30 秒自动刷新</span>
      </nav>

      <main className="app-main">
        <div style={{ display: tab === "portfolio" ? "contents" : "none" }}>
          <PortfolioCommandCenter
            backendOnline={backendOnline}
            onPendingCountChange={setPendingCount}
            autoApprove={autoApprove}
          />
        </div>
        <div style={{ display: tab === "signals" ? "contents" : "none" }}>
          <SignalsView backendOnline={backendOnline} />
        </div>
        <div style={{ display: tab === "review" ? "contents" : "none" }}>
          <StrategyReviewPanel backendOnline={backendOnline} />
        </div>
        <div style={{ display: tab === "tools" ? "contents" : "none" }}>
          <ToolsView
            backendOnline={backendOnline}
            positions={positions}
            orders={orders}
            onRefresh={refresh}
          />
        </div>
      </main>
    </div>
  );
}
