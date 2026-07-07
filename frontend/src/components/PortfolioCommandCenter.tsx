import { useState, useEffect, useCallback, useRef } from "react";
import { api } from "../api/client";
import type {
  BudgetAllocation, HoldingsResult, ScanResult,
  AgentState, PendingTrade, HoldingPosition, ScanCandidate, Position,
  PipelineStatus, GoalProgress, PortfolioDay, Account,
  AgentsStatus, AgentRunStatus, AgentRunHistoryEntry,
} from "../api/client";
import { TradeModal } from "./TradeModal";
import EarningsRadar from "./EarningsRadar";
import NarrativeRadar from "./NarrativeRadar";
import { CandleChart } from "./CandleChart";
import { KlineGatePanel } from "./KlineGatePanel";
import { DecisionChain, type DecisionInput } from "./DecisionChain";
import type { PortfolioHistory } from "../api/client";

interface Props {
  backendOnline: boolean;
  onPendingCountChange?: (n: number) => void;
  autoApprove?: { enabled: boolean };
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
  openSellSymbols: Set<string>;
  cancellingSymbols: Set<string>;
  errorSellSymbols: Set<string>;
  allOrders: import("../api/client").Order[];
}

type HoldingSortKey = "symbol" | "entry" | "price" | "today" | "value" | "alloc" | "pl" | "signal";
type SortDir = "asc" | "desc";

export function PortfolioCommandCenter({ backendOnline, onPendingCountChange, autoApprove }: Props) {
  const [data, setData] = useState<PCC>({ budget: null, holdings: null, positions: [], scan: null, agent: null, history: null, pipeline: null, agentsStatus: null, goal: null, account: null, openSellSymbols: new Set(), cancellingSymbols: new Set(), errorSellSymbols: new Set(), allOrders: [] });
  const [tradeLogTab, setTradeLogTab] = useState<"open" | "filled" | "failed" | "cancelled">("open");
  const [holdingSort, setHoldingSort] = useState<{ key: HoldingSortKey; dir: SortDir } | null>(null);
  const [detail, setDetail] = useState<DecisionInput | null>(null);   // K线+决策卡详情模态

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
    };
    setData(newData);

    // 「今日」涨跌已随 /api/positions(Alpaca change_today)返回，无需再单查 yfinance 报价
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


  // Trade log — grouped by status
  const ALPACA_OPEN = new Set(["new", "held", "accepted", "pending_new", "accepted_for_bidding"]);
  const alpacaOrderById = new Map(data.allOrders.map(o => [o.id, o]));

  const allTrades = (data.agent?.trades ?? []).filter(t => t.status !== "pending");
  const buyTradeBySymbol = new Map<string, PendingTrade>();
  (data.agent?.trades ?? [])
    .filter(t => t.side === "buy" && t.status === "executed")
    .sort((a, b) => (a.created_at < b.created_at ? 1 : -1))
    .forEach(t => {
      if (!buyTradeBySymbol.has(t.symbol)) buyTradeBySymbol.set(t.symbol, t);
    });
  const allocPctBySymbol = new Map(allocationMap.map(a => [a.symbol, a.pct]));
  const signalRank: Record<string, number> = { SELL: 4, REDUCE: 3, HOLD: 2, ADD: 1 };

  function toggleHoldingSort(key: HoldingSortKey) {
    setHoldingSort(prev => {
      if (!prev || prev.key !== key) return { key, dir: "desc" };
      if (prev.dir === "desc") return { key, dir: "asc" };
      return null;
    });
  }

  function sortHead(key: HoldingSortKey, label: string) {
    const active = holdingSort?.key === key;
    return (
      <button
        className={`pcc-rd-sort${active ? " active" : ""}`}
        onClick={() => toggleHoldingSort(key)}
        title={`${label} 排序`}
      >
        {label}
        {active && <span>{holdingSort?.dir === "desc" ? "↓" : "↑"}</span>}
      </button>
    );
  }

  function sortValue(p: HoldingPosition, key: HoldingSortKey) {
    if (key === "symbol") return p.symbol;
    if (key === "entry") return buyTradeBySymbol.get(p.symbol)?.created_at ? new Date(buyTradeBySymbol.get(p.symbol)!.created_at).getTime() : 0;
    if (key === "price") return p.current_price ?? 0;
    if (key === "today") return p.today_pct ?? -Infinity;
    if (key === "value") return p.market_value ?? 0;
    if (key === "alloc") return allocPctBySymbol.get(p.symbol) ?? 0;
    if (key === "pl") return p.unrealized_plpc ?? 0;
    return signalRank[p.sell_signal ?? "HOLD"] ?? 0;
  }

  const sortedMergedPositions = holdingSort
    ? [...mergedPositions].sort((a, b) => {
        const av = sortValue(a, holdingSort.key);
        const bv = sortValue(b, holdingSort.key);
        const cmp = typeof av === "string" && typeof bv === "string"
          ? av.localeCompare(bv)
          : Number(av) - Number(bv);
        return holdingSort.dir === "asc" ? cmp : -cmp;
      })
    : mergedPositions;

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
      } else if (t.fill_status === "canceled" || t.fill_status === "cancelled" || t.fill_status === "expired"
                 || alpacaOrder?.status === "canceled" || alpacaOrder?.status === "cancelled" || alpacaOrder?.status === "expired") {
        // 本地 fill_status 或 Alpaca 订单已撤销/过期 → 归「已撤销」，即便本地 status 还是 "executed"
        // （修僵尸：撤单后 status 未同步成 canceled，否则会误掉进「挂单中」）
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

      {/* ── Dashboard top：事件雷达（财报 + 市场叙事）→ Agent运行 ── */}
      <div className="pcc-dashboard-top">
        <div className="pcc-event-radar-grid">
          <EarningsRadar />
          <NarrativeRadar />
        </div>
        <AgentRunsPanel status={data.agentsStatus} />
      </div>

      {/* ── 方案A 两栏：持仓监控(主) + 侧栏(审批 + 交易记录)，用 grid-areas 定位 ── */}
      <div className="pcc-redesign">

        {/* 待审批（侧栏上；空时折一行） */}
        <section className={`pcc-rd-approve${pendingTrades.length === 0 ? " empty" : ""}`}>
          {pendingTrades.length === 0 ? (
            <div className="pcc-manual-collapsed-inner">
              <span className="pcc-section-title" style={{ margin: 0 }}>待你审批</span>
              <div className="pcc-manual-collapsed-msg">
                <span style={{ fontSize: 18 }}>{autoApprove?.enabled ? "⚡" : "✓"}</span>
                <span style={{ fontWeight: 600 }}>{autoApprove?.enabled ? "自主模式运行中" : "暂无待审批"}</span>
                <span style={{ fontSize: 10, color: "var(--muted)" }}>
                  {autoApprove?.enabled
                    ? "买入自动执行 · AI排雷转人工"
                    : "Rex 生成信号后出现在这里"}
                </span>
              </div>
            </div>
          ) : (
            <>
              <div className="pcc-col-header">
                <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                  <span className="pcc-section-title" style={{ margin: 0 }}>待你审批</span>
                  <span className="pcc-badge">{pendingTrades.length}</span>
                </div>
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
              </div>
              {pendingTrades.map(t => (
                <PendingCard
                  key={t.id}
                  trade={t}
                  budget={budget}
                  onApprove={() => handleApprove(t.id)}
                  onReject={() => handleReject(t.id)}
                />
              ))}
            </>
          )}
        </section>

        {/* 持仓监控（主，表格化） */}
        <section className="pcc-rd-holdings">
          <div className="pcc-rd-head">
            <span className="pcc-rd-title">📊 持仓监控</span>
            {budget && (
              <div className="pcc-rd-summary">
                <span>已投 <b>{budget.invested_pct}%</b></span>
                <span className="pcc-rd-allocbar">
                  {allocationMap.map((h, i) => (
                    <i key={h.symbol} style={{ width: `${h.pct}%`, background: `hsl(${220 + i * 30}, 60%, 55%)` }} />
                  ))}
                  <i style={{ width: `${budget.cash_pct}%`, background: "#222733" }} />
                </span>
                <span>现金 <b>{budget.cash_pct}%</b></span>
                <span className="pcc-rd-pill">持仓 {allocationMap.length}/{allocationMap.length + budget.slots_remaining}{budget.slots_remaining > 0 ? `（空${budget.slots_remaining}）` : "（满）"}</span>
                <span className="pcc-rd-pill">风险 {budget.risk_per_trade_pct}%/单</span>
              </div>
            )}
          </div>

          {alpacaPositions.length === 0 ? (
            <p className="pcc-rd-empty">暂无持仓</p>
          ) : (
            <div className="pcc-rd-table">
              <div className="pcc-rd-thead">
                <span>{sortHead("symbol", "代码")}</span>
                <span>{sortHead("entry", "入场")}</span>
                <span>{sortHead("price", "现价")}</span>
                <span>{sortHead("today", "全天")}</span>
                <span>{sortHead("value", "市值")}</span>
                <span>{sortHead("alloc", "仓位")}</span>
                <span>{sortHead("pl", "盈亏")}</span>
                <span>{sortHead("signal", "信号")}</span>
              </div>
              {sortedMergedPositions.map(p => {
                const allocPct = allocationMap.find(a => a.symbol === p.symbol)?.pct ?? 0;
                const entryTrade = buyTradeBySymbol.get(p.symbol);
                return (
                  <HoldingRow
                    key={p.symbol}
                    position={p}
                    allocPct={allocPct}
                    entryTrade={entryTrade}
                    onRefresh={load}
                    hasOpenSell={data.openSellSymbols.has(p.symbol)}
                    isCancelling={data.cancellingSymbols.has(p.symbol)}
                    hasSellError={data.errorSellSymbols.has(p.symbol)}
                    onOpenDetail={() => {
                      const buy = (data.agent?.trades ?? [])
                        .filter(t => t.symbol === p.symbol && t.side === "buy")
                        .sort((a, b) => (a.created_at < b.created_at ? 1 : -1))[0];
                      const entry = buy?.fill_price ?? buy?.price ?? p.avg_entry_price ?? null;
                      setDetail({
                        symbol: p.symbol,
                        signal: buy?.signal ?? null,
                        confidence: buy?.confidence ?? null,
                        screen_track: buy?.screen_track ?? null,
                        rsi: buy?.rsi ?? null,
                        volume_ratio: buy?.volume_ratio ?? null,
                        reason: buy?.reason ?? null,
                        stop_loss: buy?.stop_loss ?? null,
                        target_price: buy?.target_price ?? null,
                        price: entry,
                      });
                    }}
                  />
                );
              })}
            </div>
          )}
        </section>

        {/* 交易记录（侧栏下） */}
        <section className="pcc-rd-log">
          <div className="pcc-rd-log-head">
            <span className="pcc-rd-sectitle plain">交易记录</span>
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
        </section>
      </div>

      {/* 详情模态：K 线 + 决策卡 */}
      {detail && (
        <div className="pcc-modal-backdrop" onClick={() => setDetail(null)}>
          <div className="pcc-modal" onClick={e => e.stopPropagation()}>
            <div className="pcc-modal-head">
              <span className="pcc-modal-title">{detail.symbol} · K 线分析</span>
              <button className="pcc-modal-close" onClick={() => setDetail(null)}>✕</button>
            </div>
            <CandleChart
              symbol={detail.symbol}
              entryPrice={detail.price}
              stopLoss={detail.stop_loss}
              targetPrice={detail.target_price}
            />
            <KlineGatePanel symbol={detail.symbol} />
            <DecisionChain
              d={detail}
              regime={data.pipeline?.market_context?.regime ?? null}
              aggression={data.pipeline?.market_context?.aggression ?? null}
            />
          </div>
        </div>
      )}
    </div>
  );
}

// ── Sub-components ────────────────────────────────────────────────────────────


function formatEntryTime(value?: string | null) {
  if (!value) return null;
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return null;
  return new Intl.DateTimeFormat("en-US", {
    timeZone: "America/New_York",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(d).replace(",", "");
}

function holdingDay(value?: string | null) {
  if (!value) return null;
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return null;
  const fmt = new Intl.DateTimeFormat("en-CA", {
    timeZone: "America/New_York",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  });
  const toUtcDay = (s: string) => {
    const [year, month, day] = s.split("-").map(Number);
    return Date.UTC(year, month - 1, day);
  };
  const start = toUtcDay(fmt.format(d));
  const now = toUtcDay(fmt.format(new Date()));
  return Math.max(0, Math.floor((now - start) / 86_400_000));
}

function HoldingRow({ position: p, allocPct, entryTrade, onRefresh, hasOpenSell, isCancelling, hasSellError, onOpenDetail }: {
  position: HoldingPosition;
  allocPct: number;
  entryTrade?: PendingTrade;
  onRefresh: () => void;
  hasOpenSell?: boolean;
  isCancelling?: boolean;
  hasSellError?: boolean;
  onOpenDetail?: () => void;
}) {
  const [confirming, setConfirming] = useState(false);
  const [loading, setLoading] = useState(false);
  const pl = p.unrealized_pl ?? 0;
  const plPct = p.unrealized_plpc ?? 0;
  const signalColor: Record<string, string> = { SELL: "#ef4444", REDUCE: "#f97316", HOLD: "#22c55e", ADD: "#6366f1" };
  const sig = p.sell_signal ?? "HOLD";
  const todayPct = p.today_pct ?? null;
  const todayColor = todayPct != null ? (todayPct >= 0 ? "#22c55e" : "#ef4444") : "var(--muted)";
  const rthPct = p.regular_hours_pct ?? null;
  const ahPct = p.extended_hours_pct ?? null;
  const entryPrice = entryTrade?.fill_price ?? entryTrade?.price ?? p.avg_entry_price ?? null;
  const entryTime = formatEntryTime(entryTrade?.created_at);
  const entryDay = holdingDay(entryTrade?.created_at);

  async function closePos() {
    if (!confirming) { setConfirming(true); return; }
    setLoading(true);
    try { await api.closePosition(p.symbol); setTimeout(onRefresh, 1000); }
    catch (e: unknown) { alert(e instanceof Error ? e.message : "Failed"); }
    finally { setLoading(false); setConfirming(false); }
  }

  const sigColor = signalColor[sig] ?? "#64748b";
  return (
    <div className="pcc-rd-row">
      <span className="pcc-rd-c-sym">
        <span className="sym pcc-rd-symlink" onClick={onOpenDetail} title="查看 K 线 + 决策">{p.symbol}</span>
        {isCancelling ? <span className="pcc-rd-tag">撤单中</span>
          : hasOpenSell ? <span className="pcc-rd-tag">挂单</span>
          : hasSellError ? <span className="pcc-rd-tag warn">⚠</span> : null}
      </span>
      <span className="pcc-rd-entry">
        <b>{entryPrice != null ? `$${entryPrice.toFixed(2)}` : "—"}</b>
        <small>{entryTime ? `${entryTime} ET${entryDay != null ? ` · D${entryDay}` : ""}` : "时间 —"}</small>
      </span>
      <span>${p.current_price?.toFixed(2)}</span>
      <span className="pcc-rd-daymove" title="全天 = 当前价 vs 昨收；盘中 = 常规盘 vs 昨收；盘后 = 当前价 vs 常规盘收盘">
        <b style={{ color: todayColor }}>
          {todayPct != null ? `${todayPct >= 0 ? "+" : ""}${todayPct.toFixed(1)}%` : "—"}
        </b>
        {(rthPct != null || ahPct != null) && (
          <small>
            {rthPct != null && <span className={rthPct >= 0 ? "up" : "down"}>RTH {rthPct >= 0 ? "+" : ""}{rthPct.toFixed(1)}%</span>}
            {rthPct != null && ahPct != null && <span className="pcc-rd-daysep">·</span>}
            {ahPct != null && <span className={ahPct >= 0 ? "up" : "down"}>AH {ahPct >= 0 ? "+" : ""}{ahPct.toFixed(1)}%</span>}
          </small>
        )}
      </span>
      <span>${p.market_value?.toLocaleString("en-US", { maximumFractionDigits: 0 })}</span>
      <span className="pcc-rd-c-alloc">
        {allocPct.toFixed(1)}%
        <i className="pcc-rd-allocmini"><b style={{ width: `${Math.min(100, allocPct / 8 * 100)}%` }} /></i>
      </span>
      <span className={pl >= 0 ? "up" : "down"} style={{ whiteSpace: "nowrap" }}>
        {pl >= 0 ? "+" : "−"}${Math.abs(pl).toFixed(0)}{" "}
        <span style={{ color: "var(--muted)", fontWeight: 400 }}>{plPct >= 0 ? "+" : ""}{plPct.toFixed(1)}%</span>
      </span>
      <span className="pcc-rd-c-sig">
        <span className="pcc-rd-sigbadge" style={{ color: sigColor, background: sigColor + "22" }}>{sig}</span>
        {sig !== "HOLD" && (
          <button className={`trade-btn ${confirming ? "sell-btn-confirm" : "sell-btn"}`}
            onClick={closePos} disabled={loading} style={{ fontSize: 10, padding: "1px 6px" }}>
            {loading ? "…" : confirming ? "确认" : "平仓"}
          </button>
        )}
        {confirming && !loading && <button className="cancel-small-btn" onClick={() => setConfirming(false)}>✕</button>}
      </span>
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

  // v8: 仅算风险敞口($);R:R 比率已移除
  const risk   = t.price && t.stop_loss   ? (t.price - t.stop_loss) * (t.notional ? t.notional / t.price : (t.qty ?? 0)) : null;

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

      {/* v8: 风险敞口($)。R:R 比率门控已移除(v8 不按 R:R 入场,固定 -8% 止损) */}
      {risk != null && (
        <div className="pending-rr-wrap">
          <span style={{ color: "#ef4444", fontSize: 11 }}>风险 ${Math.abs(risk).toFixed(0)}(固定 -8% 止损）</span>
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

export function DashboardSummary({ history, account }: { goal: GoalProgress | null; history: PortfolioHistory | null; account: Account | null }) {
  const todayStr = new Date().toISOString().slice(0, 10);
  const days = history?.days ?? [];
  const todayDay = days.find(d => d.date === todayStr) ?? (days.length > 0 ? days[days.length - 1] : null);
  const todayPL  = todayDay?.daily_pl ?? null;
  const todayPct = todayDay?.daily_return_pct ?? null;
  const isToday  = todayDay?.date === todayStr;

  // 最近30天（滚动窗口，与下方热力图一致）
  const cutoff30 = new Date(Date.now() - 30 * 86400000).toISOString().slice(0, 10);
  const win30 = days.filter(d => d.date >= cutoff30);
  const pl30 = win30.reduce((s, d) => s + d.daily_pl, 0);
  const startEq30 = win30.length ? win30[win30.length - 1].equity - pl30 : null;
  const ret30 = startEq30 && startEq30 > 0 ? (pl30 / startEq30) * 100 : null;
  // 总收益（自开户累计）
  const totalPL  = history?.total_pl ?? null;
  const totalPct = history?.total_return_pct ?? null;
  // 现金
  const cash    = account?.cash ?? null;
  const pv      = account?.portfolio_value ?? null;
  const cashPct = (cash != null && pv) ? (cash / pv) * 100 : null;
  const cashOk  = (cash != null && pv) ? cash >= pv * 0.05 : true;

  const fmtDollar = (v: number) => `${v >= 0 ? "+" : "−"}$${Math.abs(v).toLocaleString("en-US", { maximumFractionDigits: 0 })}`;
  const fmtPct = (v: number) => `${v >= 0 ? "+" : ""}${v.toFixed(2)}%`;

  // 一个收益 KPI 卡（$ 主 + % 副）
  const ReturnKpi = ({ label, pl, pct }: { label: string; pl: number | null; pct: number | null }) => (
    <div className="pcc-summary-today">
      <div className="pcc-summary-label">{label}</div>
      <div className="pcc-summary-big">
        {pl != null ? <span className={pl >= 0 ? "up" : "down"}>{fmtDollar(pl)}</span>
                    : <span style={{ color: "var(--muted)" }}>—</span>}
        {pct != null && <span className={`pcc-summary-pct ${(pl ?? 0) >= 0 ? "up" : "down"}`}>{fmtPct(pct)}</span>}
      </div>
    </div>
  );

  return (
    <div className="pcc-summary">
      <ReturnKpi label={isToday ? "今日收益" : todayDay ? `${todayDay.date.slice(5)} 收益（休市）` : "今日收益"} pl={todayPL} pct={todayPct} />
      <ReturnKpi label="最近 30 天" pl={win30.length ? pl30 : null} pct={ret30} />
      <ReturnKpi label="总收益" pl={totalPL} pct={totalPct} />

      {/* 现金 */}
      <div className="pcc-summary-today">
        <div className="pcc-summary-label">现金</div>
        <div className="pcc-summary-big">
          {cash != null ? (
            <span style={{ color: cashOk ? "var(--text)" : "#ef4444" }}>
              ${Math.abs(cash).toLocaleString("en-US", { maximumFractionDigits: 0 })}
            </span>
          ) : <span style={{ color: "var(--muted)" }}>—</span>}
          {cashPct != null && (
            <span className="pcc-summary-pct" style={{ color: "var(--muted)" }}>{cashPct.toFixed(0)}%</span>
          )}
        </div>
        {!cashOk && <div className="pcc-summary-label" style={{ color: "#ef4444" }}>⚠ 低于储备</div>}
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

function dollarCellColor(pl: number, maxAbs: number): string {
  if (pl === 0) return "#1e293b";
  const strong = maxAbs > 0 && Math.abs(pl) / maxAbs >= 0.6;
  if (pl > 0) return strong ? "#16a34a" : "#4ade80";
  return strong ? "#dc2626" : "#fca5a5";
}

export function CompactHeatmap({
  days,
  title = "最近 30 天每日收益",
  mode = "percent",
  compact = false,
  dateFilter = "last30",
}: {
  days: PortfolioDay[];
  title?: string;
  mode?: "percent" | "dollar";
  compact?: boolean;
  dateFilter?: "last30" | "none";
}) {
  const [tooltip, setTooltip] = useState<{ day: PortfolioDay; x: number; y: number } | null>(null);
  const ref = useRef<HTMLDivElement>(null);
  const cutoff = new Date(Date.now() - 30 * 86400000).toISOString().slice(0, 10);
  const monthDays = (dateFilter === "last30" ? days.filter(d => d.date >= cutoff) : [...days])
    .sort((a, b) => a.date.localeCompare(b.date));
  const maxAbsPl = Math.max(0, ...monthDays.map(d => Math.abs(d.daily_pl)));
  if (monthDays.length === 0) return null;

  return (
    <div className={`pcc-compact-heatmap${compact ? " compact" : ""}`} ref={ref}>
      {!compact && (
        <div className="pcc-heatmap-header">
          <span className="pcc-heatmap-title">{title}</span>
          {(() => {
            const n = monthDays.length;
            const wins = monthDays.filter(d => d.daily_pl > 0).length;
            const losses = monthDays.filter(d => d.daily_pl < 0).length;
            return <span className="pcc-heatmap-summary">{n} 个交易日 · <span className="up">{wins} 盈</span> / <span className="down">{losses} 亏</span></span>;
          })()}
          <div className="pcc-heatmap-legend">
            {(mode === "percent"
              ? ([["<−1%","#dc2626"],["−1~0%","#fca5a5"],["≈0","#1e293b"],["0~1%","#4ade80"],[">1%","#16a34a"]] as const)
              : ([["亏损大","#dc2626"],["亏损","#fca5a5"],["0","#1e293b"],["盈利","#4ade80"],["盈利大","#16a34a"]] as const)
            ).map(([l,c]) => (
              <span key={l} className="pcc-legend-item">
                <span className="pcc-legend-swatch" style={{ background: c }} />{l}
              </span>
            ))}
          </div>
        </div>
      )}
      <div className="pcc-heatmap-cells">
        {monthDays.map(d => (
          <div
            key={d.date}
            className="pcc-heatmap-cell"
            style={{ background: mode === "percent" ? cellColor(d.daily_return_pct) : dollarCellColor(d.daily_pl, maxAbsPl) }}
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
            {mode === "percent" && (
              <span style={{ fontWeight: 400, marginLeft: 4 }}>
                ({tooltip.day.daily_return_pct >= 0 ? "+" : ""}{tooltip.day.daily_return_pct.toFixed(2)}%)
              </span>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

// ── Agent 运行记录：调度时间 + 健康检查 + 手动/自动标记 ────────────────────────
const AGENT_EMOJI: Record<string, string> = { maya: "🦉", scout: "🦊", rex: "🦖", vera: "🐢" };
const AGENT_COLOR: Record<string, string> = { maya: "#6366f1", scout: "#06b6d4", rex: "#f59e0b", vera: "#10b981" };
const AGENT_INTRO: Record<string, [string, string]> = {
  maya: ["读市场环境", "牛熊判断 → 定仓位上限"],
  scout: ["选股排名", "趋势门 → 按动量排序"],
  rex: ["下单执行", "买入 + 机械卖出"],
  vera: ["收盘复盘", "提取教训反哺策略"],
};
const PIPELINE_ARROW: Record<string, string> = { maya: "regime", scout: "候选股" };
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

// 取某时间戳的美东日期（YYYY-MM-DD）
function etDateOf(iso: string | null): string | null {
  if (!iso) return null;
  const s = /[Z+]/.test(iso.slice(10)) ? iso : iso + "Z";
  try {
    return new Date(s).toLocaleDateString("en-CA", { timeZone: "America/New_York" });
  } catch {
    return null;
  }
}

// 运行是否落在「市场时段窗口」内:ET [8:00, 17:00](盘前~1.5h含Maya 8:00 → 盘后1h)。
// 窗口外(凌晨/深夜)的手动测试不计入「今日」。
function inTradingWindow(iso: string | null): boolean {
  if (!iso) return false;
  const s = /[Z+]/.test(iso.slice(10)) ? iso : iso + "Z";
  try {
    const h = parseInt(new Date(s).toLocaleString("en-US", { timeZone: "America/New_York", hour: "2-digit", hour12: false }), 10) % 24;
    return h >= 8 && h < 17;
  } catch {
    return false;
  }
}

// 「今日」= 今天(ET 日期)市场窗口 [8:00,17:00] 内的运行;今天窗口内还没跑就为空(显示"今日未运行")。
function latestDayHistory(history: AgentRunHistoryEntry[]): AgentRunHistoryEntry[] {
  const todayEt = etDateOf(new Date().toISOString());
  return history.filter(h => inTradingWindow(h.ran_at) && etDateOf(h.ran_at) === todayEt);
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
      {(() => {
        const pipe = ["maya", "scout", "rex"]
          .map((id) => status.agents.find((x: AgentRunStatus) => x.id === id))
          .filter(Boolean) as AgentRunStatus[];
        const vera = status.agents.find((x: AgentRunStatus) => x.id === "vera");

        const node = (a: AgentRunStatus) => {
          const dayHist = latestDayHistory(a.history);
          const fails = dayHist.filter((h) => h.result === "fail").length;
          const failErr = dayHist.find((h) => h.result === "fail")?.error;
          const [title, detail] = AGENT_INTRO[a.id] ?? [a.name, ""];
          const icon = a.status === "ok" ? "✅" : a.status === "waiting" ? "⏳" : a.status === "idle" ? "⚪" : "⚠️";
          const reason = a.status !== "ok"
            ? a.status_label
            : fails > 0 ? `⚠️ 今日 ${fails} 次失败${failErr ? "：" + failErr.slice(0, 36) : ""}` : null;
          return (
            <div key={a.id} className={`agent-pipe-node run-${a.status}`}
                 style={{ borderLeftColor: AGENT_COLOR[a.id] ?? "var(--border)" }}
                 title={`${a.role}\n调度 ${a.scheduled_times_et.join(" / ")} ET`}>
              <div className="agent-pipe-top">
                <span className="agent-run-emoji" style={{ borderColor: AGENT_COLOR[a.id], background: `${AGENT_COLOR[a.id]}1f` }}>{AGENT_EMOJI[a.id]}</span>
                <span className="agent-pipe-name" style={{ color: AGENT_COLOR[a.id] }}>{a.name}</span>
                <span className="agent-pipe-icon">{icon}</span>
              </div>
              <div className="agent-pipe-intro">{title}<span className="agent-pipe-detail">{detail}</span></div>
              <div className="agent-pipe-when">{dayHist.length > 0 ? `${fmtEtTime(dayHist[0].ran_at)} · 今日 ${dayHist.length} 次` : "今日未运行"}</div>
              {reason && <div className="agent-pipe-reason" style={{ color: RUN_STATUS_COLOR[a.status] ?? "#f59e0b" }}>{reason}</div>}
            </div>
          );
        };

        return (
          <>
            <div className="agent-pipeline">
              {pipe.flatMap((a, i) =>
                i < pipe.length - 1
                  ? [node(a), (
                      <div key={a.id + "-arr"} className="agent-pipe-arrow">
                        <span className="agent-pipe-arrow-label">{PIPELINE_ARROW[a.id]}</span>
                        <span className="agent-pipe-arrow-mark" style={{ color: AGENT_COLOR[a.id] }}>→</span>
                      </div>
                    )]
                  : [node(a)]
              )}
            </div>
            <div className="agent-vera-strip" style={{ borderLeftColor: AGENT_COLOR.vera }} title={vera?.role ?? "收盘复盘,提取策略教训反哺选股"}>
              <span className="agent-run-emoji" style={{ borderColor: AGENT_COLOR.vera, background: `${AGENT_COLOR.vera}1f` }}>{AGENT_EMOJI.vera}</span>
              <span className="agent-pipe-name" style={{ color: AGENT_COLOR.vera }}>{vera?.name ?? "Vera"}</span>
              <span className="agent-vera-intro">{AGENT_INTRO.vera[0]} <span className="agent-pipe-detail" style={{ display: "inline" }}>↻ {AGENT_INTRO.vera[1]}</span></span>
              <span className="agent-vera-when">{vera?.last_run_at ? fmtEtTime(vera.last_run_at) : "手动触发"}</span>
            </div>
          </>
        );
      })()}
    </div>
  );
}
