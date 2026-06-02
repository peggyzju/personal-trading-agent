import { useState, useEffect, useCallback, useRef } from "react";
import { api } from "../api/client";
import type {
  BudgetAllocation, HoldingsResult, ScanResult,
  AgentState, PendingTrade, HoldingPosition, ScanCandidate, Position,
  PipelineStatus, GoalProgress, Quote, PortfolioDay, Account,
  AgentsStatus, AgentRunStatus, AgentRunHistoryEntry,
} from "../api/client";
import { TradeModal } from "./TradeModal";
import type { PortfolioHistory } from "../api/client";

interface Props {
  backendOnline: boolean;
  onPendingCountChange?: (n: number) => void;
  autoApprove?: { enabled: boolean; threshold: number };
}

// ── Data loader ───────────────────────────────────────────────────────────────

interface PCC {
  budget: BudgetAllocation | null;
  holdings: HoldingsResult | null;
  positions: Position[];
  scan: ScanResult | null;
  agent: AgentState | null;
  history: PortfolioHistory | null;
  pipeline: PipelineStatus | null;
  agentsStatus: AgentsStatus | null;
  goal: GoalProgress | null;
  account: Account | null;
  quotes: Record<string, Quote>;
  openSellSymbols: Set<string>;
  cancellingSymbols: Set<string>;
  errorSellSymbols: Set<string>;
  allOrders: import("../api/client").Order[];
}

export function PortfolioCommandCenter({ backendOnline, onPendingCountChange, autoApprove }: Props) {
  const [data, setData] = useState<PCC>({ budget: null, holdings: null, positions: [], scan: null, agent: null, history: null, pipeline: null, agentsStatus: null, goal: null, account: null, quotes: {}, openSellSymbols: new Set(), cancellingSymbols: new Set(), errorSellSymbols: new Set(), allOrders: [] });
  const [tradeLogTab, setTradeLogTab] = useState<"open" | "filled" | "failed" | "cancelled">("open");

  const load = useCallback(async () => {
    if (!backendOnline) return;
    const [budget, holdings, positions, scan, agent, history, pipeline, agentsStatus, goal, account, orders] = await Promise.allSettled([
      api.getBudget(),
      api.getHoldings(),
      api.getPositions(),
      api.getScan(),
      api.getAgentState(),
      api.getPortfolioHistory(),
      api.getPipelineStatus(),
      api.getAgentsStatus(),
      api.getGoalProgress(),
      api.getAccount(),
      api.getOrders(),
    ]);
    const allOrders = orders.status === "fulfilled" ? orders.value : [];
    const agentTrades: import("../api/client").PendingTrade[] = agent.status === "fulfilled" ? agent.value.trades : [];
    // Only Rex-initiated sell orders count as "挂单中" — bracket child legs (take-profit / stop-loss)
    // are auto-created by Alpaca and their IDs never appear in our executed_order_id list.
    const ourSellOrderIds = new Set(
      agentTrades
        .filter(t => t.side === "sell" && t.executed_order_id)
        .map(t => t.executed_order_id as string)
    );
    const openSellSymbols = new Set(
      allOrders
        .filter((o: import("../api/client").Order) =>
          o.side === "sell" &&
          ["new", "accepted", "pending_new", "pending_cancel"].includes(o.status) &&
          ourSellOrderIds.has(o.id)
        )
        .map((o: import("../api/client").Order) => o.symbol)
    );
    const cancellingSymbols = new Set(
      allOrders
        .filter((o: import("../api/client").Order) => o.side === "sell" && o.status === "pending_cancel")
        .map((o: import("../api/client").Order) => o.symbol)
    );
    const todayStr = new Date().toISOString().slice(0, 10);
    const errorSellSymbols = new Set(
      agentTrades
        .filter((t: import("../api/client").PendingTrade) => t.status === "error" && t.side === "sell" && t.created_at.startsWith(todayStr))
        .map((t: import("../api/client").PendingTrade) => t.symbol)
    );
    const newData = {
      budget: budget.status === "fulfilled" ? budget.value : null,
      holdings: holdings.status === "fulfilled" ? holdings.value : null,
      positions: positions.status === "fulfilled" ? positions.value : [],
      scan: scan.status === "fulfilled" ? scan.value : null,
      agent: agent.status === "fulfilled" ? agent.value : null,
      history: history.status === "fulfilled" ? history.value : null,
      pipeline: pipeline.status === "fulfilled" ? pipeline.value : null,
      agentsStatus: agentsStatus.status === "fulfilled" ? agentsStatus.value : null,
      goal: goal.status === "fulfilled" ? goal.value : null,
      account: account.status === "fulfilled" ? account.value : null,
      openSellSymbols,
      cancellingSymbols,
      errorSellSymbols,
      allOrders,
      quotes: {},
    };
    setData(newData);

    // Fetch quotes in background — don't block the main data render
    const positionSymbols = newData.positions.map(p => p.symbol);
    if (positionSymbols.length > 0) {
      Promise.allSettled(positionSymbols.map(sym => api.getQuoteSingle(sym))).then(results => {
        const quotes: Record<string, Quote> = {};
        results.forEach((r, i) => {
          if (r.status === "fulfilled") quotes[positionSymbols[i]] = r.value;
        });
        setData(prev => ({ ...prev, quotes }));
      });
    }
    if (onPendingCountChange && newData.agent) {
      onPendingCountChange(newData.agent.trades.filter(t => t.status === "pending").length);
    }
  }, [backendOnline, onPendingCountChange]);

  useEffect(() => {
    load();
    const id = setInterval(load, 20_000);
    return () => clearInterval(id);
  }, [load]);

  async function handleApprove(id: string) {
    try {
      const updated = await api.approveTrade(id);
      setData(prev => prev.agent ? {
        ...prev,
        agent: { ...prev.agent, trades: prev.agent.trades.map(t => t.id === id ? updated : t) },
      } : prev);
      setTimeout(load, 1500);
    } catch (e: unknown) { alert(e instanceof Error ? e.message : "Failed"); }
  }

  function handleReject(id: string) {
    // Optimistic: remove immediately from UI
    setData(prev => prev.agent ? {
      ...prev,
      agent: { ...prev.agent, trades: prev.agent.trades.filter(t => t.id !== id) },
    } : prev);
    api.rejectTrade(id).catch(() => {});
  }

  if (!backendOnline) {
    return <div className="brief-offline">Start the backend to view Portfolio Command Center.</div>;
  }

  const budget = data.budget;
  // Use raw Alpaca positions for allocation (source of truth)
  const alpacaPositions = data.positions;
  const portfolioValue = budget?.portfolio_value ?? 0;
  // Build allocation from real positions
  const allocationMap = alpacaPositions.map(p => ({
    symbol: p.symbol,
    market_value: p.market_value,
    pct: portfolioValue > 0 ? Math.round(p.market_value / portfolioValue * 1000) / 10 : 0,
    unrealized_pl: p.unrealized_pl,
    unrealized_plpc: p.unrealized_plpc,
  }));

  // Merge sell signals from holdings analysis into alpaca positions (alpaca is always source of truth)
  const holdingsMap = Object.fromEntries(
    (data.holdings?.positions ?? []).map(p => [p.symbol, p])
  );
  const mergedPositions: HoldingPosition[] = alpacaPositions.map(p => ({
    ...p,
    sell_signal: holdingsMap[p.symbol]?.sell_signal,
    urgency:     holdingsMap[p.symbol]?.urgency,
    reason:      holdingsMap[p.symbol]?.reason,
    suggested_action: holdingsMap[p.symbol]?.suggested_action,
  }));

  const pendingTrades = (data.agent?.trades ?? []).filter(t => t.status === "pending");
  // Top 10 scan candidates (all signals, not filtered)
  const scanCandidates = (data.scan?.candidates ?? []).slice(0, 10);

  // Signal lists
  const pendingSymbols = new Set(pendingTrades.map(t => t.symbol + t.side));
  // Only show sell signals for symbols actually in the real Alpaca account
  const alpacaSymbols = new Set(alpacaPositions.map(p => p.symbol));
  void mergedPositions.filter(p =>
    alpacaSymbols.has(p.symbol) &&
    (p.sell_signal === "SELL" || p.sell_signal === "REDUCE") &&
    !pendingSymbols.has(p.symbol + "sell")
  );
  void scanCandidates
    .filter(c => !pendingSymbols.has(c.symbol + "buy"))
    .sort((a, b) => (a.owned ? 1 : 0) - (b.owned ? 1 : 0));

  // Agent log entries (most recent 5)
  const agentLogEntries = (data.agent?.log ?? []).slice(0, 5);

  // Trade log — grouped by status
  const ALPACA_OPEN = new Set(["new", "held", "accepted", "pending_new", "accepted_for_bidding"]);
  const alpacaOrderById = new Map(data.allOrders.map(o => [o.id, o]));

  const allTrades = (data.agent?.trades ?? []).filter(t => t.status !== "pending");

  // Categorise each trade
  const grouped = allTrades.reduce<{
    open: PendingTrade[]; filled: PendingTrade[]; failed: PendingTrade[]; cancelled: PendingTrade[];
  }>((acc, t) => {
    if (t.status === "cancelled" || t.status === "expired") {
      acc.cancelled.push(t);
    } else if (t.status === "rejected" || t.status === "error") {
      acc.failed.push(t);
    } else if (t.status === "executed") {
      const alpacaOrder = t.executed_order_id ? alpacaOrderById.get(t.executed_order_id) : undefined;
      // If we already have local fill data, it's definitely filled
      if (t.fill_status === "filled" || t.fill_price != null) {
        acc.filled.push(t);
      } else if (alpacaOrder?.status === "filled") {
        acc.filled.push(t);
      } else if (alpacaOrder?.status === "canceled" || alpacaOrder?.status === "cancelled" || alpacaOrder?.status === "expired") {
        // Order was cancelled in Alpaca — show under 已撤销 even if local status says "executed"
        acc.cancelled.push(t);
      } else if (alpacaOrder && ALPACA_OPEN.has(alpacaOrder.status)) {
        acc.open.push(t);
      } else {
        // No conclusive info — treat as open (order submitted, awaiting fill confirmation)
        acc.open.push(t);
      }
    }
    return acc;
  }, { open: [], filled: [], cancelled: [], failed: [] });

  return (
    <div className="pcc-container">

      {/* ── Dashboard top ── */}
      <div className="pcc-dashboard-top">
        <DashboardSummary goal={data.goal} history={data.history} account={data.account} />
        {(data.history?.days.length ?? 0) > 10 && (
          <CompactHeatmap days={data.history!.days} />
        )}
        <AgentRunsPanel status={data.agentsStatus} />
      </div>

      {/* ── Zone labels ── */}
      <div className="pcc-zone-row">
        <div className="pcc-zone-label">
          {autoApprove?.enabled ? (
            <>
              <span className="zone-chip zone-chip-auto">⚡ 自动执行中</span>
              <span className="zone-desc">置信度 ≥{Math.round((autoApprove.threshold) * 100)}% 自动执行，低于阈值仍需确认</span>
            </>
          ) : (
            <>
              <span className="zone-chip zone-chip-manual">👤 需要你决策</span>
              <span className="zone-desc">Rex 已分析，等待人工确认</span>
            </>
          )}
        </div>
        <div className="pcc-zone-label">
          <span className="zone-chip zone-chip-auto">⚡ 自动化管理</span>
          <span className="zone-desc">Agent 实时监控，无需干预</span>
        </div>
      </div>

      {/* ── 2-col: Pending (manual) | Holdings (auto) ── */}
      <div className="pcc-main-cols">

        {/* LEFT — Manual: pending approval */}
        <div className="pcc-manual-col">
          <div className="pcc-col-header">
            <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
              <span className="pcc-section-title" style={{ margin: 0 }}>待你审批</span>
              {pendingTrades.length > 0 && (
                <span className="pcc-badge">{pendingTrades.length}</span>
              )}
            </div>
            {pendingTrades.length > 0 && (
              <div className="pcc-bulk-btns">
                <button className="pcc-bulk-btn pcc-bulk-btn-approve"
                  onClick={() => pendingTrades.forEach(t => handleApprove(t.id))}>
                  ✓ 全部批准
                </button>
                <button className="pcc-bulk-btn pcc-bulk-btn-reject"
                  onClick={() => pendingTrades.forEach(t => handleReject(t.id))}>
                  ✕ 全部拒绝
                </button>
              </div>
            )}
          </div>

          {pendingTrades.length === 0 ? (
            <div className="pcc-manual-empty">
              <div style={{ fontSize: 24, marginBottom: 8 }}>{autoApprove?.enabled ? "⚡" : "✓"}</div>
              <div>{autoApprove?.enabled ? "自动执行已开启" : "暂无待审批交易"}</div>
              <div style={{ marginTop: 4, fontSize: 11, color: "var(--muted)" }}>
                {autoApprove?.enabled
                  ? `置信度 ≥${Math.round(autoApprove.threshold * 100)}% 的信号将自动执行`
                  : "Rex 生成信号后将出现在这里"}
              </div>
            </div>
          ) : (
            pendingTrades.map(t => (
              <PendingCard
                key={t.id}
                trade={t}
                budget={budget}
                onApprove={() => handleApprove(t.id)}
                onReject={() => handleReject(t.id)}
              />
            ))
          )}
        </div>

        {/* RIGHT — Auto: holdings + sell signals */}
        <div className="pcc-auto-col">
          <div className="pcc-col-header">
            <span className="pcc-section-title" style={{ margin: 0 }}>持仓监控</span>
            <span className="pcc-auto-tag">⚡ Rex 持续监控中</span>
          </div>

          {/* Allocation bar */}
          <div className="pcc-alloc-bar-wrap">
            {allocationMap.map((h, i) => (
              <div key={h.symbol} className="pcc-alloc-segment"
                style={{ width: `${h.pct}%`, background: `hsl(${220 + i * 35}, 65%, 55%)` }}
                title={`${h.symbol} ${h.pct}%`} />
            ))}
            {budget && (
              <div className="pcc-alloc-segment"
                style={{ width: `${budget.cash_pct}%`, background: "#1e293b" }}
                title={`现金 ${budget.cash_pct}%`} />
            )}
          </div>
          <div style={{ display: "flex", justifyContent: "space-between", fontSize: 10, color: "var(--muted)", marginTop: -4 }}>
            <span>已投 {budget?.invested_pct ?? 0}%</span>
            <span>现金 {budget?.cash_pct ?? 0}%</span>
          </div>

          {alpacaPositions.length === 0 ? (
            <p style={{ color: "var(--muted)", fontSize: 12 }}>暂无持仓</p>
          ) : (
            <div className="pcc-holdings-list">
              {mergedPositions.map(p => {
                const allocPct = allocationMap.find(a => a.symbol === p.symbol)?.pct ?? 0;
                return (
                  <HoldingRow
                    key={p.symbol}
                    position={p}
                    quote={data.quotes[p.symbol] ?? null}
                    allocPct={allocPct}
                    onRefresh={load}
                    hasOpenSell={data.openSellSymbols.has(p.symbol)}
                    isCancelling={data.cancellingSymbols.has(p.symbol)}
                    hasSellError={data.errorSellSymbols.has(p.symbol)}
                  />
                );
              })}
            </div>
          )}

          {budget && (
            <div className="pcc-slots">
              {allocationMap.map((h, i) => (
                <div key={h.symbol} className="pcc-slot-pill pcc-slot-filled"
                  style={{ borderColor: `hsl(${220 + i * 35}, 65%, 55%)40` }}>
                  <span>{h.symbol}</span>
                  <span className="pcc-slot-pct">{h.pct}%</span>
                </div>
              ))}
              {Array.from({ length: budget.slots_remaining }).map((_, i) => (
                <div key={i} className="pcc-slot-pill pcc-slot-empty">空</div>
              ))}
            </div>
          )}

          {budget && (
            <div style={{ display: "flex", gap: 14, paddingTop: 8, borderTop: "1px solid var(--border)" }}>
              <div className="pcc-stat">
                <span className="holding-label">可用现金</span>
                <span className="pcc-stat-val">${budget.cash.toLocaleString()}</span>
              </div>
              <div className="pcc-stat">
                <span className="holding-label">建议投入</span>
                <span className="pcc-stat-val up">
                  ${(data.budget?.suggested_buys ?? []).reduce((s, b) => s + b.cost, 0).toLocaleString()}
                </span>
              </div>
              <div className="pcc-stat">
                <span className="holding-label">每笔风险</span>
                <span className="pcc-stat-val">{budget.risk_per_trade_pct}%</span>
              </div>
            </div>
          )}
        </div>
      </div>

      {/* ── Activity log 2-col ── */}
      <div className="pcc-activity-cols">

        {/* Agent log */}
        <div className="pcc-log-card">
          <div className="pcc-log-header">
            <span className="pcc-section-title" style={{ margin: 0 }}>Agent 执行记录</span>
            <span className="pcc-log-tag pcc-log-tag-auto">⚡ 全自动</span>
            <span style={{ fontSize: 10, color: "var(--muted)" }}>Maya · Scout · Rex · Vera</span>
          </div>
          <div className="pcc-log-list">
            {agentLogEntries.length === 0 ? (
              <div className="pcc-log-item dimmed">
                <div className="pcc-log-icon pcc-log-icon-sys">—</div>
                <div className="pcc-log-body">
                  <span className="pcc-log-title" style={{ color: "var(--muted)" }}>暂无记录</span>
                  <span className="pcc-log-sub">等待 Agent 首次运行</span>
                </div>
              </div>
            ) : (
              agentLogEntries.map((entry, i) => {
                const sources = entry.sources?.join(" · ") ?? "";
                const regimeInfo = entry.regime ? ` · ${entry.regime}` : "";
                return (
                  <div key={i} className="pcc-log-item">
                    <div className={`pcc-log-icon ${entry.status === "error" ? "pcc-log-icon-sell" : "pcc-log-icon-agent"}`}>
                      {entry.status === "error" ? "⚠" : "🧠"}
                    </div>
                    <div className="pcc-log-body">
                      <span className="pcc-log-title">
                        Rex · {entry.signals_found} 个信号{entry.trades_queued > 0 ? ` · ${entry.trades_queued} 待审` : ""}
                      </span>
                      <span className="pcc-log-sub">{sources}{regimeInfo}</span>
                      <span className="pcc-log-time">{new Date(entry.run_at).toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" })}</span>
                    </div>
                  </div>
                );
              })
            )}
            {data.pipeline?.review.status !== "done" && (
              <div className="pcc-log-item dimmed">
                <div className="pcc-log-icon pcc-log-icon-sys">📈</div>
                <div className="pcc-log-body">
                  <span className="pcc-log-title">Vera · 策略复盘</span>
                  <span className="pcc-log-sub">等待今日收盘后生成</span>
                  <span className="pcc-log-time">16:00 预计</span>
                </div>
              </div>
            )}
          </div>
        </div>

        {/* Trade log — grouped */}
        <div className="pcc-log-card">
          <div className="pcc-log-header">
            <span className="pcc-section-title" style={{ margin: 0 }}>交易记录</span>
          </div>

          {/* Group filter tabs */}
          <div style={{ display: "flex", gap: 6, padding: "0 12px 10px", borderBottom: "1px solid var(--border)", flexWrap: "wrap" }}>
            {(["open", "filled", "failed", "cancelled"] as const).map(tab => {
              const labels: Record<string, string> = { open: "挂单中", filled: "已成交", failed: "失败/拒绝", cancelled: "已撤销" };
              const counts = { open: grouped.open.length, filled: grouped.filled.length, failed: grouped.failed.length, cancelled: grouped.cancelled.length };
              const active = tradeLogTab === tab;
              const colors: Record<string, string> = { open: "#60a5fa", filled: "#22c55e", failed: "#ef4444", cancelled: "#64748b" };
              return (
                <button key={tab} onClick={() => setTradeLogTab(tab)} style={{
                  fontSize: 11, padding: "3px 9px", borderRadius: 6, border: "1px solid",
                  borderColor: active ? colors[tab] : "var(--border)",
                  background: active ? colors[tab] + "22" : "transparent",
                  color: active ? colors[tab] : "var(--muted)",
                  cursor: "pointer", display: "flex", alignItems: "center", gap: 4,
                  fontWeight: active ? 600 : 400,
                }}>
                  {labels[tab]}
                  {counts[tab] > 0 && (
                    <span style={{
                      background: active ? colors[tab] : "#334155",
                      color: active ? "#fff" : "var(--muted)",
                      borderRadius: 10, padding: "0 5px", fontSize: 10, fontWeight: 700,
                    }}>{counts[tab]}</span>
                  )}
                </button>
              );
            })}
          </div>

          <div className="pcc-log-list">
            {grouped[tradeLogTab].length === 0 ? (
              <div className="pcc-log-item dimmed">
                <div className="pcc-log-icon pcc-log-icon-sys">—</div>
                <div className="pcc-log-body">
                  <span className="pcc-log-title" style={{ color: "var(--muted)" }}>
                    {{ open: "暂无挂单", filled: "暂无成交记录", failed: "无失败/拒绝记录", cancelled: "无撤销记录" }[tradeLogTab]}
                  </span>
                </div>
              </div>
            ) : (
              grouped[tradeLogTab].map(t => {
                const isBuy = t.side === "buy";
                const isErr = t.status === "error";
                const isRej = t.status === "rejected";
                const isCancelled = t.status === "cancelled" || t.status === "expired";
                const alpacaOrder = t.executed_order_id ? alpacaOrderById.get(t.executed_order_id) : undefined;

                let iconClass = isBuy ? "pcc-log-icon-buy" : "pcc-log-icon-sell";
                let iconChar = isBuy ? "↑" : "↓";
                if (isErr) { iconClass = "pcc-log-icon-sys"; iconChar = "⚠"; }
                else if (isRej || isCancelled) { iconClass = "pcc-log-icon-sys"; iconChar = "✕"; }

                const alpacaCancelled = alpacaOrder?.status === "canceled" || alpacaOrder?.status === "cancelled" || alpacaOrder?.status === "expired";
                let subText = "";
                if (isErr) subText = t.error ? `失败: ${t.error.slice(0, 60)}` : "提交失败";
                else if (isRej) subText = t.reason ? t.reason.slice(0, 60) : "已拒绝";
                else if (isCancelled || alpacaCancelled) subText = t.status === "expired" || alpacaOrder?.status === "expired" ? "已过期" : "已撤销";
                else if (t.fill_price != null) subText = `成交均价 $${t.fill_price.toFixed(2)}`;
                else if (alpacaOrder?.status === "filled") subText = `成交均价 $${alpacaOrder.filled_avg_price?.toFixed(2) ?? "—"}`;
                else if (tradeLogTab === "open") subText = "挂单等待成交";
                else subText = "成交均价获取中…";

                return (
                  <div key={t.id} className="pcc-log-item" style={{ opacity: isCancelled || isRej ? 0.55 : 1 }}>
                    <div className={`pcc-log-icon ${iconClass}`}>{iconChar}</div>
                    <div className="pcc-log-body">
                      <span className="pcc-log-title" style={{ textDecoration: isRej || isCancelled ? "line-through" : undefined }}>
                        <span style={{ color: isBuy ? "#22c55e" : "#ef4444" }}>{isBuy ? "买入" : "卖出"}</span>
                        {" · "}{t.symbol}
                        {t.notional ? ` $${t.notional.toFixed(0)}` : t.qty ? ` ×${t.qty}` : ""}
                        {t.price ? <span style={{ color: "var(--muted)", fontWeight: 400 }}> @ ${t.price.toFixed(2)}</span> : null}
                      </span>
                      <span className="pcc-log-sub">{subText}</span>
                      <span className="pcc-log-time">
                        {new Date(t.created_at).toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" })}
                        {" · "}{new Date(t.created_at).toLocaleDateString("zh-CN", { month: "numeric", day: "numeric" })}
                      </span>
                    </div>
                  </div>
                );
              })
            )}
          </div>
        </div>

      </div>
    </div>
  );
}

// ── Sub-components ────────────────────────────────────────────────────────────


function HoldingRow({ position: p, quote, allocPct, onRefresh, hasOpenSell, isCancelling, hasSellError }: {
  position: HoldingPosition;
  quote: Quote | null;
  allocPct: number;
  onRefresh: () => void;
  hasOpenSell?: boolean;
  isCancelling?: boolean;
  hasSellError?: boolean;
}) {
  const [confirming, setConfirming] = useState(false);
  const [loading, setLoading] = useState(false);
  const pl = p.unrealized_pl ?? 0;
  const plPct = p.unrealized_plpc ?? 0;
  const signalColor: Record<string, string> = { SELL: "#ef4444", REDUCE: "#f97316", HOLD: "#22c55e", ADD: "#6366f1" };
  const sig = p.sell_signal ?? "HOLD";
  const todayPct = quote?.change_pct ?? null;
  const todayColor = todayPct != null ? (todayPct >= 0 ? "#22c55e" : "#ef4444") : "var(--muted)";

  async function closePos() {
    if (!confirming) { setConfirming(true); return; }
    setLoading(true);
    try { await api.closePosition(p.symbol); setTimeout(onRefresh, 1000); }
    catch (e: unknown) { alert(e instanceof Error ? e.message : "Failed"); }
    finally { setLoading(false); setConfirming(false); }
  }

  return (
    <div className="pcc-holding-row">
      {/* Row 1: symbol + signal + today's change + close button */}
      <div className="pcc-holding-main">
        <span className="symbol" style={{ fontSize: 14 }}>{p.symbol}</span>
        {sig !== "HOLD" && (
          <span className="signal-badge" style={{ background: signalColor[sig] ?? "#64748b", fontSize: 11, padding: "1px 6px" }}>{sig}</span>
        )}
        {isCancelling ? (
          <span style={{ fontSize: 10, color: "#64748b", background: "#1e293b", border: "1px solid #334155", borderRadius: 4, padding: "1px 5px" }}>撤单中</span>
        ) : hasOpenSell ? (
          <span style={{ fontSize: 10, color: "#94a3b8", background: "#1e293b", border: "1px solid #334155", borderRadius: 4, padding: "1px 5px" }}>挂单中</span>
        ) : hasSellError ? (
          <span style={{ fontSize: 10, color: "#f97316", background: "#1e293b", border: "1px solid #f9731640", borderRadius: 4, padding: "1px 5px" }}>⚠ 提交失败</span>
        ) : null}
        {todayPct != null && (
          <span style={{ color: todayColor, fontSize: 12, fontWeight: 600 }}>
            {todayPct >= 0 ? "+" : ""}{todayPct.toFixed(2)}% 今日
          </span>
        )}
        {sig !== "HOLD" && (
          <div style={{ marginLeft: "auto", display: "flex", gap: 4 }}>
            <button
              className={`trade-btn ${confirming ? "sell-btn-confirm" : "sell-btn"}`}
              onClick={closePos}
              disabled={loading}
            >
              {loading ? "…" : confirming ? "确认平仓" : "平仓"}
            </button>
            {confirming && !loading && (
              <button className="cancel-small-btn" onClick={() => setConfirming(false)}>✕</button>
            )}
          </div>
        )}
      </div>
      {/* Row 2: price · qty · market value · alloc% · total P&L */}
      <div className="pcc-holding-detail">
        <span>${p.current_price?.toFixed(2)}</span>
        <span className="pcc-holding-dot">·</span>
        <span>{typeof p.qty === "number" ? (Number.isInteger(p.qty) ? p.qty.toLocaleString("en-US") : p.qty.toLocaleString("en-US", { maximumFractionDigits: 2 })) : p.qty} 股</span>
        <span className="pcc-holding-dot">·</span>
        <span>${p.market_value?.toLocaleString("en-US", { maximumFractionDigits: 0 })}</span>
        <span className="pcc-holding-dot">·</span>
        <span style={{ color: "#93c5fd" }}>{allocPct.toFixed(1)}% 仓位</span>
        <span className="pcc-holding-dot">·</span>
        <span className={pl >= 0 ? "up" : "down"}>
          {pl >= 0 ? "+" : ""}${pl.toFixed(0)} ({plPct >= 0 ? "+" : ""}{plPct.toFixed(1)}%)
        </span>
      </div>
    </div>
  );
}

function PendingCard({
  trade: t, budget, onApprove, onReject,
}: { trade: PendingTrade; budget: BudgetAllocation | null; onApprove: () => void; onReject: () => void }) {
  const [confirming, setConfirming] = useState(false);
  const [approving, setApproving] = useState(false);

  async function handleApproveClick() {
    setApproving(true);
    try { await onApprove(); } finally { setApproving(false); }
  }
  const expiresIn = Math.max(0, Math.round((new Date(t.expires_at).getTime() - Date.now()) / 60000));
  const portfolioValue = budget?.portfolio_value ?? 100_000;

  // Risk/reward calc
  const risk   = t.price && t.stop_loss   ? (t.price - t.stop_loss) * (t.notional ? t.notional / t.price : (t.qty ?? 0)) : null;
  const reward = t.price && t.target_price ? (t.target_price - t.price) * (t.notional ? t.notional / t.price : (t.qty ?? 0)) : null;
  const rrRatio = risk && reward && risk > 0 ? (reward / risk) : null;

  // Price drift check
  const driftPct = t.price_drift_pct ?? 0;
  const hasDrift = Math.abs(driftPct) > 0.5;

  const sideColor = t.side === "buy" ? "#22c55e" : "#ef4444";

  return (
    <div className="pending-card">
      {/* Layer 1: Header */}
      <div className="pending-card-header">
        <div className="pending-card-left">
          <span className={`pending-side-badge ${t.side}`}>{t.side === "buy" ? "买入" : "卖出"}</span>
          <strong className="pending-symbol">{t.symbol}</strong>
          <span className="signal-badge" style={{ background: sideColor, fontSize: 11, padding: "2px 8px" }}>{t.signal}</span>
          {t.universe && (
            <span className="pending-universe-badge" style={{ background: t.universe === "nasdaq100" ? "#7c3aed20" : "#1e40af20", color: t.universe === "nasdaq100" ? "#a78bfa" : "#93c5fd" }}>
              {t.universe === "sp500" ? "S&P" : t.universe === "nasdaq100" ? "NQ" : t.universe}
            </span>
          )}
          <span style={{ color: "var(--muted)", fontSize: 11 }}>来源: {t.source}</span>
          <span style={{ color: expiresIn < 30 ? "#f59e0b" : "var(--muted)", fontSize: 11 }}>过期 {expiresIn}m</span>
        </div>
        <div className="pending-card-actions">
          {confirming ? (
            <>
              <button className="trade-btn buy-btn" style={{ width: "auto", margin: 0, padding: "5px 16px", background: approving ? "#555" : sideColor, opacity: approving ? 0.7 : 1 }}
                onClick={handleApproveClick} disabled={approving}>
                {approving ? "执行中…" : "✓ 确认执行"}
              </button>
              {!approving && <button className="cancel-small-btn" onClick={() => setConfirming(false)}>✕</button>}
            </>
          ) : (
            <>
              <button className="trade-btn buy-btn" style={{ width: "auto", margin: 0, padding: "5px 16px", background: sideColor }}
                onClick={() => setConfirming(true)}>
                ✓ 批准
              </button>
              <button className="cancel-small-btn" style={{ padding: "4px 12px", fontSize: 12 }} onClick={onReject}>✕ 拒绝</button>
            </>
          )}
        </div>
      </div>

      {/* Layer 2: Price stats grid */}
      <div className="pending-stats-grid">
        <div className="pending-stat">
          <span className="pending-stat-label">金额</span>
          <span className="pending-stat-val">{t.notional ? `$${t.notional.toFixed(0)}` : t.qty ? `${t.qty}股` : "—"}</span>
          {t.notional && portfolioValue > 0 && (
            <span className="pending-stat-sub">{(t.notional / portfolioValue * 100).toFixed(1)}% 仓位</span>
          )}
        </div>
        <div className="pending-stat">
          <span className="pending-stat-label">入场价</span>
          <span className="pending-stat-val">{t.price ? `$${t.price.toFixed(2)}` : "市价"}</span>
        </div>
        <div className="pending-stat">
          <span className="pending-stat-label">止损</span>
          <span className="pending-stat-val" style={{ color: "#ef4444" }}>{t.stop_loss ? `$${t.stop_loss.toFixed(2)}` : "—"}</span>
          {t.price && t.stop_loss && (
            <span className="pending-stat-sub">-{((t.price - t.stop_loss) / t.price * 100).toFixed(1)}%</span>
          )}
        </div>
        <div className="pending-stat">
          <span className="pending-stat-label">目标价</span>
          <span className="pending-stat-val" style={{ color: "#22c55e" }}>{t.target_price ? `$${t.target_price.toFixed(2)}` : "—"}</span>
          {t.price && t.target_price && (
            <span className="pending-stat-sub">+{((t.target_price - t.price) / t.price * 100).toFixed(1)}%</span>
          )}
        </div>
        <div className="pending-stat">
          <span className="pending-stat-label">置信度</span>
          <span className="pending-stat-val" style={{ color: t.confidence >= 0.8 ? "#22c55e" : t.confidence >= 0.65 ? "#f59e0b" : "#ef4444" }}>
            {Math.round(t.confidence * 100)}%
          </span>
        </div>
      </div>

      {/* Layer 3: Price drift warning */}
      {hasDrift && (
        <div className="pending-drift-warn">
          ⚠ 价格已偏移 {driftPct >= 0 ? "+" : ""}{driftPct.toFixed(2)}% — 入场价可能不准确
        </div>
      )}

      {/* Layer 4: Technical indicators */}
      {(t.rsi != null || t.momentum_5d != null || t.volume_ratio != null || t.near_breakout != null) && (
        <div className="pending-tech-grid">
          {t.rsi != null && (
            <div className="pending-tech-item">
              <span className="pending-tech-label">RSI(14)</span>
              <span className="pending-tech-val" style={{ color: t.rsi > 70 ? "#ef4444" : t.rsi < 30 ? "#22c55e" : "#f59e0b" }}>
                {t.rsi.toFixed(1)}
              </span>
            </div>
          )}
          {t.momentum_5d != null && (
            <div className="pending-tech-item">
              <span className="pending-tech-label">5日动量</span>
              <span className="pending-tech-val" style={{ color: t.momentum_5d >= 0 ? "#22c55e" : "#ef4444" }}>
                {t.momentum_5d >= 0 ? "+" : ""}{t.momentum_5d.toFixed(1)}%
              </span>
            </div>
          )}
          {t.volume_ratio != null && (
            <div className="pending-tech-item">
              <span className="pending-tech-label">量比</span>
              <span className="pending-tech-val" style={{ color: t.volume_ratio >= 1.5 ? "#22c55e" : "var(--text)" }}>
                {t.volume_ratio.toFixed(1)}x
              </span>
            </div>
          )}
          {t.near_breakout != null && (
            <div className="pending-tech-item">
              <span className="pending-tech-label">突破信号</span>
              <span className="pending-tech-val" style={{ color: t.near_breakout ? "#22c55e" : "var(--muted)" }}>
                {t.near_breakout ? "✓ 近突破" : "否"}
              </span>
            </div>
          )}
        </div>
      )}

      {/* Layer 5: Risk/Reward bar */}
      {rrRatio != null && risk != null && reward != null && (
        <div className="pending-rr-wrap">
          <div className="pending-rr-bar-row">
            <span style={{ color: "#ef4444", fontSize: 11 }}>风险 ${Math.abs(risk).toFixed(0)}</span>
            <div className="pending-rr-bar">
              <div className="pending-rr-loss" style={{ width: `${Math.min(50, 50 / rrRatio)}%` }} />
              <div className="pending-rr-gain" style={{ width: `${Math.min(50, 50 * (rrRatio > 1 ? 1 : rrRatio))}%` }} />
            </div>
            <span style={{ color: "#22c55e", fontSize: 11 }}>收益 ${Math.abs(reward).toFixed(0)}</span>
          </div>
          <div className="pending-rr-ratio">
            <span style={{ color: rrRatio >= 2 ? "#22c55e" : rrRatio >= 1.5 ? "#f59e0b" : "#ef4444", fontWeight: 700 }}>
              R:R = 1 : {rrRatio.toFixed(1)}
            </span>
          </div>
        </div>
      )}

      {/* Layer 6: AI reason */}
      {t.reason && (
        <div className="pending-ai-reason">
          <span className="pending-ai-icon">🤖</span>
          <span>{t.reason}</span>
        </div>
      )}
    </div>
  );
}

// @ts-ignore -- reserved for future sell signal UI
function _SellSignalRow({ position: p, onRefresh }: { position: HoldingPosition; onRefresh: () => void }) {
  const [confirming, setConfirming] = useState(false);
  const [loading, setLoading] = useState(false);

  async function closePos() {
    if (!confirming) { setConfirming(true); return; }
    setLoading(true);
    try { await api.closePosition(p.symbol); setTimeout(onRefresh, 1000); }
    catch (e: unknown) { alert(e instanceof Error ? e.message : "Failed"); }
    finally { setLoading(false); setConfirming(false); }
  }

  return (
    <div className="pcc-signal-row">
      <div className="pcc-signal-left">
        <span className="down">▼</span>
        <strong>{p.symbol}</strong>
        <span className="signal-badge" style={{ background: p.sell_signal === "SELL" ? "#ef4444" : "#f97316", fontSize: 11, padding: "1px 6px" }}>
          {p.sell_signal}
        </span>
        {p.urgency && (
          <span style={{ color: p.urgency === "HIGH" ? "#ef4444" : "#f59e0b", fontSize: 11 }}>
            {p.urgency}
          </span>
        )}
        <span style={{ color: "var(--muted)", fontSize: 11 }}>${p.current_price?.toFixed(2)}</span>
      </div>
      <div className="pcc-signal-right">
        <button
          className={`trade-btn ${confirming ? "sell-btn-confirm" : "sell-btn"}`}
          onClick={closePos}
          disabled={loading}
        >
          {loading ? "…" : confirming ? "确认平仓" : "平仓"}
        </button>
        {confirming && !loading && (
          <button className="cancel-small-btn" onClick={() => setConfirming(false)}>✕</button>
        )}
      </div>
      {p.reason && <p className="pcc-signal-reason">{p.reason}</p>}
    </div>
  );
}

const SIGNAL_BG: Record<string, string> = {
  STRONG_BUY: "#16a34a",
  BUY:        "#22c55e",
  HOLD:       "#64748b",
  SELL:       "#ef4444",
  WATCH:      "#f59e0b",
};

type PccSection = "ai" | "sentiment" | null;

// @ts-ignore -- reserved for future buy signal UI
function _BuySignalRow({ rank, candidate: c, budget, backendOnline }: { rank: number; candidate: ScanCandidate; budget: BudgetAllocation | null; backendOnline: boolean }) {
  const [showModal, setShowModal] = useState(false);
  const [section, setSection] = useState<PccSection>(null);
  const [aiResult, setAiResult] = useState<import("../api/client").Analysis | null>(null);
  const [aiLoading, setAiLoading] = useState(false);
  const [sentiment, setSentiment] = useState<import("../api/client").NewsSentiment | null>(null);
  const [sentLoading, setSentLoading] = useState(false);

  const isBuyable = (c.signal === "STRONG_BUY" || c.signal === "BUY") && !c.owned;
  const portfolioValue = budget?.portfolio_value ?? 100_000;
  const stop = c.stop_loss ?? (c.price ? c.price * 0.97 : undefined);
  const suggestedNotional = isBuyable && stop && c.price && stop < c.price
    ? Math.min(portfolioValue * 0.02 / (c.price - stop) * c.price, portfolioValue * 0.10)
    : null;

  function toggleSection(key: PccSection) {
    const next = section === key ? null : key;
    setSection(next);
    if (next === "ai" && !aiResult && !aiLoading) {
      setAiLoading(true);
      api.analyze(c.symbol).then(r => { setAiResult(r); setAiLoading(false); }).catch(() => setAiLoading(false));
    }
    if (next === "sentiment" && !sentiment && !sentLoading) {
      setSentLoading(true);
      api.analyzeNewsSentiment(c.symbol).then(r => { setSentiment(r); setSentLoading(false); }).catch(() => setSentLoading(false));
    }
  }

  const SIG_COLOR: Record<string, string> = { BUY: "#22c55e", SELL: "#ef4444", HOLD: "#64748b" };

  return (
    <div className={`pcc-signal-row ${isBuyable ? "pcc-row-buy" : "pcc-row-neutral"}`}>
      {showModal && (
        <TradeModal
          symbol={c.symbol}
          side="buy"
          suggestedPrice={c.price}
          stopLoss={c.stop_loss}
          targetPrice={c.target_price}
          onClose={() => setShowModal(false)}
          onSuccess={() => setShowModal(false)}
        />
      )}
      <div className="pcc-signal-left">
        <span style={{ color: "var(--muted)", fontSize: 12, minWidth: 20 }}>#{rank}</span>
        <strong style={{ opacity: isBuyable ? 1 : 0.65 }}>{c.symbol}</strong>
        <span className="signal-badge" style={{
          background: SIGNAL_BG[c.signal] ?? "#64748b",
          fontSize: 11, padding: "1px 6px",
          opacity: isBuyable ? 1 : 0.7,
        }}>
          {c.signal?.replace("_", " ")}
        </span>
        {c.owned && (
          <span style={{ fontSize: 11, background: "#1e40af", color: "#93c5fd", borderRadius: 4, padding: "2px 6px", fontWeight: 600 }}>
            已持仓
          </span>
        )}
        <span style={{ color: "var(--muted)", fontSize: 11 }}>AI {c.ai_score}/10</span>
        <span style={{ color: "var(--muted)", fontSize: 11 }}>${c.price?.toFixed(2)}</span>
        {suggestedNotional && (
          <span style={{ color: "#f59e0b", fontSize: 11, fontWeight: 600 }}>
            推荐 ${suggestedNotional.toFixed(0)}
          </span>
        )}
      </div>
      <div className="pcc-signal-right">
        {stop && c.price && isBuyable && (
          <span style={{ color: "#ef4444", fontSize: 11 }}>止损 ${stop.toFixed(2)}</span>
        )}
        {c.target_price && isBuyable && (
          <span style={{ color: "#22c55e", fontSize: 11 }}>目标 ${c.target_price.toFixed(2)}</span>
        )}
        {isBuyable ? (
          <button className="trade-btn buy-btn" style={{ width: "auto", margin: 0, padding: "4px 14px" }}
            onClick={() => setShowModal(true)}>
            买入
          </button>
        ) : c.owned ? (
          <span style={{ color: "#93c5fd", fontSize: 12 }}>持仓中</span>
        ) : (
          <span style={{ color: "var(--muted)", fontSize: 12 }}>观察</span>
        )}
      </div>
      {c.reason && <p className="pcc-signal-reason" style={{ opacity: isBuyable ? 1 : 0.6 }}>{c.reason}</p>}

      {/* Analysis tabs — always visible for owned stocks, optional for others */}
      <div style={{ display: "flex", gap: 6, marginTop: 6 }}>
        <button
          onClick={() => toggleSection("ai")}
          disabled={!backendOnline}
          style={{
            fontSize: 11, padding: "2px 10px", borderRadius: 4, border: "none", cursor: "pointer",
            background: section === "ai" ? "#3730a3" : "#1e293b",
            color: section === "ai" ? "#a5b4fc" : "var(--muted)",
          }}>
          🤖 AI 分析
        </button>
        <button
          onClick={() => toggleSection("sentiment")}
          disabled={!backendOnline}
          style={{
            fontSize: 11, padding: "2px 10px", borderRadius: 4, border: "none", cursor: "pointer",
            background: section === "sentiment" ? "#3730a3" : "#1e293b",
            color: section === "sentiment" ? "#a5b4fc" : "var(--muted)",
          }}>
          📰 舆情
        </button>
      </div>

      {section === "ai" && (
        <div style={{ marginTop: 8, padding: "10px 12px", background: "#0f172a", borderRadius: 6, fontSize: 12 }}>
          {aiLoading && <span style={{ color: "var(--muted)" }}>分析中…</span>}
          {aiResult && (
            <>
              <div style={{ display: "flex", gap: 12, marginBottom: 8, flexWrap: "wrap" }}>
                <span style={{ color: SIG_COLOR[aiResult.signal] ?? "#f59e0b", fontWeight: 700 }}>{aiResult.signal}</span>
                <span style={{ color: "var(--muted)" }}>信心 {Math.round(aiResult.confidence * 100)}%</span>
                {aiResult.target_price && <span style={{ color: "#22c55e" }}>目标 ${aiResult.target_price.toFixed(2)}</span>}
                {aiResult.stop_loss && <span style={{ color: "#ef4444" }}>止损 ${aiResult.stop_loss.toFixed(2)}</span>}
              </div>
              <p style={{ color: "#cbd5e1", margin: "0 0 6px", lineHeight: 1.5 }}>{aiResult.reasoning}</p>
              {aiResult.key_risks?.length > 0 && (
                <div>
                  <span style={{ color: "var(--muted)", fontSize: 11 }}>风险：</span>
                  {aiResult.key_risks.map((r, i) => (
                    <span key={i} style={{ color: "#f59e0b", fontSize: 11, marginLeft: 4 }}>• {r}</span>
                  ))}
                </div>
              )}
            </>
          )}
        </div>
      )}

      {section === "sentiment" && (
        <div style={{ marginTop: 8, padding: "10px 12px", background: "#0f172a", borderRadius: 6, fontSize: 12 }}>
          {sentLoading && <span style={{ color: "var(--muted)" }}>加载舆情…</span>}
          {sentiment && (
            <>
              <div style={{ display: "flex", gap: 10, marginBottom: 8, flexWrap: "wrap" }}>
                <span style={{ color: sentiment.overall === "BULLISH" ? "#22c55e" : sentiment.overall === "BEARISH" ? "#ef4444" : "#f59e0b", fontWeight: 700 }}>
                  {sentiment.overall}
                </span>
                <span style={{ color: "#cbd5e1" }}>{sentiment.key_insight}</span>
              </div>
              {sentiment.watch_for && (
                <p style={{ color: "#f59e0b", margin: "0 0 6px" }}>⚠️ {sentiment.watch_for}</p>
              )}
              {sentiment.items?.slice(0, 3).map((item, i) => (
                <div key={i} style={{ borderTop: "1px solid #1e293b", paddingTop: 6, marginTop: 6 }}>
                  <a href={item.url} target="_blank" rel="noreferrer"
                    style={{ color: "#93c5fd", fontWeight: 600, textDecoration: "none" }}>
                    {item.title}
                  </a>
                  <p style={{ color: "var(--muted)", margin: "2px 0 0", fontSize: 11 }}>{item.summary}</p>
                </div>
              ))}
            </>
          )}
        </div>
      )}
    </div>
  );
}

// ── Dashboard Top Components ──────────────────────────────────────────────────

function DashboardSummary({ goal, history, account }: { goal: GoalProgress | null; history: PortfolioHistory | null; account: Account | null }) {
  const todayStr = new Date().toISOString().slice(0, 10);
  const days = history?.days ?? [];
  const todayDay = days.find(d => d.date === todayStr) ?? (days.length > 0 ? days[days.length - 1] : null);
  const todayPL  = todayDay?.daily_pl ?? null;
  const todayPct = todayDay?.daily_return_pct ?? null;
  const isToday  = todayDay?.date === todayStr;

  const goalPct   = goal?.current_return_pct ?? null;
  const fillPct   = goal ? Math.min(100, Math.max(0, goal.current_return_pct / goal.target_pct_high * 100)) : 0;
  const timePct   = goal ? Math.round(goal.days_elapsed / goal.total_days * 100) : 0;
  const loMark    = goal ? Math.round(goal.target_pct_low / goal.target_pct_high * 100) : 67;
  const barColor  = !goal ? "#818cf8" : goal.on_track ? "#22c55e" : goalPct != null && goalPct >= 0 ? "#f59e0b" : "#ef4444";

  const totalPL    = history?.total_pl ?? null;
  const totalPct   = history?.total_return_pct ?? null;
  const totalColor = totalPL != null ? (totalPL >= 0 ? "#22c55e" : "#ef4444") : "var(--muted)";

  return (
    <div className="pcc-summary">
      {/* left: today */}
      <div className="pcc-summary-today">
        <div className="pcc-summary-label">{isToday ? "今日收益" : todayDay ? `${todayDay.date.slice(5)} 收益（休市）` : "今日收益"}</div>
        <div className="pcc-summary-big">
          {todayPL != null ? (
            <span className={todayPL >= 0 ? "up" : "down"}>
              {todayPL >= 0 ? "+" : "−"}${Math.abs(todayPL).toLocaleString("en-US", { maximumFractionDigits: 0 })}
            </span>
          ) : <span style={{ color: "var(--muted)" }}>—</span>}
          {todayPct != null && (
            <span className={`pcc-summary-pct ${todayPL != null && todayPL >= 0 ? "up" : "down"}`}>
              {todayPct >= 0 ? "+" : ""}{todayPct.toFixed(2)}%
            </span>
          )}
        </div>
      </div>

      {/* center: goal progress */}
      <div className="pcc-summary-goal">
        <div className="pcc-summary-goal-top">
          <span className="pcc-summary-label">目标期收益</span>
          <span className="pcc-summary-label" style={{ color: "var(--muted)" }}>
            {goal ? `第 ${goal.days_elapsed}/${goal.total_days} 天` : ""}
          </span>
        </div>
        <div className="pcc-summary-track">
          <div className="pcc-summary-time-ghost" style={{ width: `${timePct}%` }} />
          <div className="pcc-summary-fill" style={{ width: `${fillPct}%`, background: barColor }} />
          <div className="pcc-summary-mark" style={{ left: `${loMark}%` }} title={`最低目标 ${goal?.target_pct_low}%`} />
        </div>
        <div className="pcc-summary-goal-bottom">
          <span style={{ display: "flex", alignItems: "baseline", gap: 6 }}>
            <span style={{ color: barColor, fontSize: 13, fontWeight: 700 }}>
              {goalPct != null ? `${goalPct >= 0 ? "+" : ""}${goalPct.toFixed(2)}%` : "—"}
            </span>
            {totalPL != null && (
              <span style={{ color: totalColor, fontSize: 11, fontWeight: 600 }}>
                {totalPL >= 0 ? "+" : "−"}${Math.abs(totalPL).toLocaleString("en-US", { maximumFractionDigits: 0 })}
              </span>
            )}
            {goal && (
              <span style={{ color: "var(--muted)", fontWeight: 400, fontSize: 11 }}>
                · 目标 {goal.target_pct_low}–{goal.target_pct_high}%
              </span>
            )}
          </span>
          {goal && !goal.on_track && (
            <span style={{ color: "#f59e0b", fontSize: 11, fontWeight: 600 }}>⚠ 需 +{goal.daily_return_needed.toFixed(2)}%/天</span>
          )}
          {goal && goal.on_track && (
            <span style={{ color: "#22c55e", fontSize: 11, fontWeight: 600 }}>✓ 达标轨道</span>
          )}
        </div>
      </div>

      {/* right: target + cash */}
      <div className="pcc-summary-target">
        <div className="pcc-summary-label">{goal ? `${goal.total_days}天目标` : "目标"}</div>
        <div className="pcc-summary-big" style={{ fontSize: 18 }}>
          {goal ? `${goal.target_pct_low.toFixed(0)}–${goal.target_pct_high.toFixed(0)}%` : "—"}
        </div>
        {goal && (
          <div className="pcc-summary-label" style={{ color: "var(--muted)" }}>
            剩 {goal.days_remaining} 天
          </div>
        )}
        {account != null && (() => {
          const cash = account.cash;
          const reserve = account.portfolio_value * 0.05;
          const cashOk = cash >= reserve;
          const rows = [
            {
              label: "现金",
              value: `${cash >= 0 ? "" : "−"}$${Math.abs(cash).toLocaleString("en-US", { maximumFractionDigits: 0 })}`,
              color: cashOk ? "var(--text)" : "#ef4444",
              badge: cashOk ? null : "⚠ 低于储备",
              badgeColor: "#ef4444",
            },
            {
              label: "日内次数",
              value: `${account.daytrade_count} 次`,
              color: "var(--text)",
              badge: null,
              badgeColor: "",
            },
          ];
          return (
            <div className="pcc-account-rows">
              {rows.map(r => (
                <div key={r.label} className="pcc-account-row">
                  <span className="pcc-summary-label">{r.label}</span>
                  <span style={{ fontSize: 11, fontWeight: 600, color: r.color }}>{r.value}</span>
                  {r.badge && <span style={{ fontSize: 10, color: r.badgeColor }}>{r.badge}</span>}
                </div>
              ))}
            </div>
          );
        })()}
      </div>
    </div>
  );
}

function cellColor(pct: number): string {
  if (pct < -1)   return "#dc2626";
  if (pct < 0)    return "#fca5a5";
  if (pct < 0.05) return "#1e293b";
  if (pct < 1)    return "#4ade80";
  return "#16a34a";
}

function CompactHeatmap({ days }: { days: PortfolioDay[] }) {
  const [tooltip, setTooltip] = useState<{ day: PortfolioDay; x: number; y: number } | null>(null);
  const ref = useRef<HTMLDivElement>(null);
  const now = new Date();
  const monthKey = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}`;
  const monthDays = days.filter(d => d.date.startsWith(monthKey));
  if (monthDays.length === 0) return null;

  return (
    <div className="pcc-compact-heatmap" ref={ref}>
      <div className="pcc-heatmap-header">
        <span className="pcc-heatmap-title">本月每日收益</span>
        <div className="pcc-heatmap-legend">
          {([["<−1%","#dc2626"],["−1~0%","#fca5a5"],["≈0","#1e293b"],["0~1%","#4ade80"],[">1%","#16a34a"]] as const).map(([l,c]) => (
            <span key={l} className="pcc-legend-item">
              <span className="pcc-legend-swatch" style={{ background: c }} />{l}
            </span>
          ))}
        </div>
      </div>
      <div className="pcc-heatmap-cells">
        {monthDays.map(d => (
          <div
            key={d.date}
            className="pcc-heatmap-cell"
            style={{ background: cellColor(d.daily_return_pct) }}
            onMouseEnter={e => {
              const rect = ref.current?.getBoundingClientRect();
              if (rect) setTooltip({ day: d, x: e.clientX - rect.left, y: e.clientY - rect.top });
            }}
            onMouseLeave={() => setTooltip(null)}
          />
        ))}
      </div>
      {tooltip && (
        <div className="pcc-heatmap-tooltip" style={{ left: tooltip.x + 10, top: Math.max(0, tooltip.y - 56) }}>
          <div style={{ fontSize: 11, color: "var(--muted)" }}>
            {new Date(tooltip.day.date + "T12:00").toLocaleDateString("zh-CN", { month: "numeric", day: "numeric", weekday: "short" })}
          </div>
          <div className={tooltip.day.daily_pl >= 0 ? "up" : "down"} style={{ fontWeight: 700 }}>
            {tooltip.day.daily_pl >= 0 ? "+" : "−"}${Math.abs(tooltip.day.daily_pl).toFixed(0)}
            <span style={{ fontWeight: 400, marginLeft: 4 }}>
              ({tooltip.day.daily_return_pct >= 0 ? "+" : ""}{tooltip.day.daily_return_pct.toFixed(2)}%)
            </span>
          </div>
        </div>
      )}
    </div>
  );
}

// ── Agent 运行记录：调度时间 + 健康检查 + 手动/自动标记 ────────────────────────
const AGENT_EMOJI: Record<string, string> = { maya: "🧠", scout: "🔍", rex: "⚡" };
const AGENT_COLOR: Record<string, string> = { maya: "#6366f1", scout: "#06b6d4", rex: "#f59e0b" };
const RUN_STATUS_COLOR: Record<string, string> = {
  ok: "#22c55e", waiting: "#64748b", missed: "#ef4444", idle: "#475569", never: "#ef4444",
};

// 运行记录时间统一显示美东时间（系统内部存 UTC；朴素时间戳补 Z 按 UTC 解析）
function fmtEtTime(iso: string | null): string {
  if (!iso) return "—";
  const s = /[Z+]/.test(iso.slice(10)) ? iso : iso + "Z";
  try {
    return new Date(s).toLocaleTimeString("en-US", {
      timeZone: "America/New_York", hour: "2-digit", minute: "2-digit", hour12: false,
    }) + " ET";
  } catch {
    return "—";
  }
}

function AgentRunsPanel({ status }: { status: AgentsStatus | null }) {
  if (!status) return null;
  return (
    <div className="agent-runs-panel">
      <div className="agent-runs-head">
        <span className="agent-runs-title">📋 Agent 运行记录</span>
        <span className="agent-runs-sub">
          {status.is_trading_day ? "交易日" : "非交易日"} · 美东时间 单一调度
        </span>
      </div>
      <div className="agent-runs-grid">
        {status.agents.map((a: AgentRunStatus) => (
          <div key={a.id} className={`agent-run-card run-${a.status}`}>
            <div className="agent-run-top">
              <span className="agent-run-emoji">{AGENT_EMOJI[a.id] ?? "🤖"}</span>
              <span className="agent-run-name" style={{ color: AGENT_COLOR[a.id] ?? "var(--text)" }}>
                {a.name}
              </span>
              {a.trigger && (
                <span className={`agent-run-trigger trigger-${a.trigger}`}>
                  {a.trigger === "manual" ? "👤 手动" : "🤖 自动"}
                </span>
              )}
            </div>
            <div className="agent-run-role">{a.role}</div>
            <div className="agent-run-meta">
              <span className="agent-run-sched">🕐 {a.scheduled_times_et.join(" / ")} ET</span>
              {a.cadence_note && <span className="agent-run-cadence">{a.cadence_note}</span>}
            </div>
            <div className="agent-run-status-row">
              <span className="agent-run-health" style={{ color: RUN_STATUS_COLOR[a.status] ?? "var(--text)" }}>
                {a.status_label}
              </span>
              <span className="agent-run-age">
                {a.last_run_at ? `${fmtEtTime(a.last_run_at)} · ${a.age ?? ""}` : (a.age ?? "—")}
              </span>
            </div>
            <div className="agent-run-history">
              <div className="agent-run-history-title">运行历史</div>
              {a.history.length === 0 ? (
                <div className="agent-run-history-empty">暂无记录</div>
              ) : (
                a.history.map((h: AgentRunHistoryEntry, i: number) => (
                  <div key={i} className="agent-run-history-row" title={h.error ?? undefined}>
                    <span className="agent-run-history-time">{fmtEtTime(h.ran_at)}</span>
                    <span className="agent-run-history-age">{h.age ?? "—"}</span>
                    {h.trigger && (
                      <span className={`agent-run-tag tag-${h.trigger}`}>
                        {h.trigger === "manual" ? "👤 手动" : "🤖 自动"}
                      </span>
                    )}
                    {h.result && (
                      <span className={`agent-run-tag result-${h.result}`}>
                        {h.result === "success" ? "✓ 成功" : "✗ 失败"}
                      </span>
                    )}
                  </div>
                ))
              )}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

