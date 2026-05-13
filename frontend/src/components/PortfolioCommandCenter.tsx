import { useState, useEffect, useCallback } from "react";
import { api } from "../api/client";
import type {
  BudgetAllocation, HoldingsResult, ScanResult,
  AgentState, PendingTrade, HoldingPosition, ScanCandidate, Position,
  MarketRegime, CircuitBreaker,
} from "../api/client";
import { TradeModal } from "./TradeModal";
import { CalendarHeatmap } from "./PortfolioOverview";
import type { PortfolioHistory } from "../api/client";

interface Props { backendOnline: boolean }

// ── Data loader ───────────────────────────────────────────────────────────────

interface PCC {
  budget: BudgetAllocation | null;
  holdings: HoldingsResult | null;
  positions: Position[];
  scan: ScanResult | null;
  agent: AgentState | null;
  history: PortfolioHistory | null;
  regime: MarketRegime | null;
  breaker: CircuitBreaker | null;
}

export function PortfolioCommandCenter({ backendOnline }: Props) {
  const [data, setData] = useState<PCC>({ budget: null, holdings: null, positions: [], scan: null, agent: null, history: null, regime: null, breaker: null });
  const [resettingBreaker, setResettingBreaker] = useState(false);
  const [agentRunning, setAgentRunning] = useState(false);
  const [scanning, setScanning] = useState(false);
  const [autoApprove, setAutoApproveState] = useState<{ enabled: boolean; threshold: number }>({ enabled: false, threshold: 0.80 });
  const [autoApproveLoading, setAutoApproveLoading] = useState(false);

  const load = useCallback(async () => {
    if (!backendOnline) return;
    const [budget, holdings, positions, scan, agent, history, regime, breaker] = await Promise.allSettled([
      api.getBudget(),
      api.getHoldings(),
      api.getPositions(),
      api.getScan(),
      api.getAgentState(),
      api.getPortfolioHistory(),
      api.getMarketRegime(),
      api.getCircuitBreaker(),
    ]);
    setData({
      budget: budget.status === "fulfilled" ? budget.value : null,
      holdings: holdings.status === "fulfilled" ? holdings.value : null,
      positions: positions.status === "fulfilled" ? positions.value : [],
      scan: scan.status === "fulfilled" ? scan.value : null,
      agent: agent.status === "fulfilled" ? agent.value : null,
      history: history.status === "fulfilled" ? history.value : null,
      regime: regime.status === "fulfilled" ? regime.value : null,
      breaker: breaker.status === "fulfilled" ? breaker.value : null,
    });
  }, [backendOnline]);

  useEffect(() => {
    if (backendOnline) {
      api.getAutoApprove().then(setAutoApproveState).catch(() => {});
    }
  }, [backendOnline]);

  useEffect(() => {
    load();
    const id = setInterval(load, 20_000);
    return () => clearInterval(id);
  }, [load]);

  async function runAgent() {
    setAgentRunning(true);
    try {
      await api.runAgent();
      // poll for new results
      let tries = 0;
      const poll = setInterval(async () => {
        await load();
        if (++tries > 30) { clearInterval(poll); setAgentRunning(false); }
      }, 2000);
      setTimeout(() => { clearInterval(poll); setAgentRunning(false); }, 90_000);
    } catch { setAgentRunning(false); }
  }

  async function runScan() {
    setScanning(true);
    try {
      await api.triggerScan();
      const poll = setInterval(async () => {
        const scan = await api.getScan();
        setData(prev => ({ ...prev, scan }));
        if (scan.status !== "running") { clearInterval(poll); setScanning(false); }
      }, 3000);
      setTimeout(() => { clearInterval(poll); setScanning(false); }, 120_000);
    } catch { setScanning(false); }
  }

  async function toggleAutoApprove() {
    setAutoApproveLoading(true);
    try {
      const next = !autoApprove.enabled;
      const cfg = await api.setAutoApprove(next, autoApprove.threshold);
      setAutoApproveState(cfg);
    } catch { /* ignore */ }
    finally { setAutoApproveLoading(false); }
  }

  async function resetBreaker() {
    setResettingBreaker(true);
    try {
      const breaker = await api.resetCircuitBreaker();
      setData(prev => ({ ...prev, breaker }));
    } catch { /* ignore */ }
    finally { setResettingBreaker(false); }
  }

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

  async function handleReject(id: string) {
    try {
      const updated = await api.rejectTrade(id);
      setData(prev => prev.agent ? {
        ...prev,
        agent: { ...prev.agent, trades: prev.agent.trades.map(t => t.id === id ? updated : t) },
      } : prev);
    } catch (e: unknown) { alert(e instanceof Error ? e.message : "Failed"); }
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

  // For sell signals, merge Alpaca prices into holdings sell signal data
  const holdingsPositions = data.holdings?.positions ?? [];
  const alpacaMap = Object.fromEntries(alpacaPositions.map(p => [p.symbol, p]));
  const mergedPositions = holdingsPositions.map(p => ({
    ...p,
    ...(alpacaMap[p.symbol] ?? {}),  // overwrite prices with live Alpaca data
  }));

  const pendingTrades = (data.agent?.trades ?? []).filter(t => t.status === "pending");
  // Top 10 scan candidates (all signals, not filtered)
  const scanCandidates = (data.scan?.candidates ?? []).slice(0, 10);

  // Signal lists
  const pendingSymbols = new Set(pendingTrades.map(t => t.symbol + t.side));
  // Only show sell signals for symbols actually in the real Alpaca account
  const alpacaSymbols = new Set(alpacaPositions.map(p => p.symbol));
  const sellSignals = mergedPositions.filter(p =>
    alpacaSymbols.has(p.symbol) &&
    (p.sell_signal === "SELL" || p.sell_signal === "REDUCE") &&
    !pendingSymbols.has(p.symbol + "sell")
  );
  const buySignals = scanCandidates.filter(c => !pendingSymbols.has(c.symbol + "buy"));

  return (
    <div className="pcc-container">
      {/* ── Top bar ── */}
      <div className="pcc-topbar">
        <div className="pcc-stat">
          <span className="holding-label">组合总值</span>
          <span className="pcc-stat-val">${(budget?.portfolio_value ?? 0).toLocaleString()}</span>
        </div>
        <div className="pcc-stat">
          <span className="holding-label">现金</span>
          <span className="pcc-stat-val up">${(budget?.cash ?? 0).toLocaleString()} <span className="pcc-pct">({budget?.cash_pct ?? 0}%)</span></span>
        </div>
        <div className="pcc-stat">
          <span className="holding-label">已投资</span>
          <span className="pcc-stat-val">${(budget?.invested ?? 0).toLocaleString()} <span className="pcc-pct">({budget?.invested_pct ?? 0}%)</span></span>
        </div>
        <div className="pcc-stat">
          <span className="holding-label">空仓位</span>
          <span className="pcc-stat-val" style={{ color: "#f59e0b" }}>{budget?.slots_remaining ?? "—"} 个</span>
        </div>
        {data.regime && <RegimeBadge regime={data.regime} />}
        {data.breaker && <CircuitBreakerBadge breaker={data.breaker} onReset={resetBreaker} resetting={resettingBreaker} />}
        <div className="pcc-actions">
          <div className="pcc-agent-block">
            <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
              <button className="brief-generate-btn" onClick={runAgent} disabled={agentRunning}>
                {agentRunning ? "分析中…" : "🤖 运行 Agent"}
              </button>
              {/* Auto-approve toggle */}
              <button
                className={`pcc-auto-approve-btn${autoApprove.enabled ? " active" : ""}`}
                onClick={toggleAutoApprove}
                disabled={autoApproveLoading}
                title={autoApprove.enabled
                  ? `自动执行已开启 (置信度 ≥ ${Math.round(autoApprove.threshold * 100)}%)，点击关闭`
                  : "点击开启自动执行高置信度交易"}
              >
                {autoApprove.enabled
                  ? `⚡ 自动执行 ≥${Math.round(autoApprove.threshold * 100)}%`
                  : "⚡ 手动审批"}
              </button>
            </div>
            {data.agent?.log[0] && (
              <span className="pcc-last-run">
                上次运行&nbsp;
                {new Date(data.agent.log[0].run_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}
                {" · "}
                {data.agent.log[0].trades_queued} 个信号
                {(data.agent.log[0] as any).auto_approved
                  ? ` · ⚡${(data.agent.log[0] as any).auto_approved} 自动执行` : ""}
              </span>
            )}
            {!data.agent?.log[0] && (
              <span className="pcc-last-run pcc-last-run-none">自动 8:30 AM ET 开市前运行</span>
            )}
          </div>
          <button className="brief-regenerate-btn" onClick={runScan} disabled={scanning}>
            {scanning ? "扫描中…" : "🔍 扫描 S&P"}
          </button>
          <button className="brief-regenerate-btn" onClick={load}>↺</button>
        </div>
      </div>

      {/* ── Two-column layout ── */}
      <div className="pcc-body">

        {/* Left: current holdings */}
        <div className="pcc-left">
          <h3 className="pcc-section-title">持仓分布</h3>

          {/* Allocation bar — from live Alpaca positions */}
          <div className="pcc-alloc-bar-wrap">
            {allocationMap.map((h, i) => (
              <div
                key={h.symbol}
                className="pcc-alloc-segment"
                style={{ width: `${h.pct}%`, background: `hsl(${240 + i * 35}, 60%, 55%)` }}
                title={`${h.symbol} ${h.pct}%`}
              />
            ))}
            {budget && (
              <div
                className="pcc-alloc-segment"
                style={{ width: `${budget.cash_pct}%`, background: "#1e293b" }}
                title={`Cash ${budget.cash_pct}%`}
              />
            )}
          </div>

          {alpacaPositions.length === 0 ? (
            <p className="brief-empty-text" style={{ padding: "20px 0", fontSize: 13 }}>
              暂无持仓（Alpaca paper 账户）
            </p>
          ) : (
            <div className="pcc-holdings-list">
              {mergedPositions.map(p => (
                <HoldingRow key={p.symbol} position={p} onRefresh={load} />
              ))}
            </div>
          )}

          {/* Slot pills */}
          {budget && (
            <div className="pcc-slots">
              {allocationMap.map((h, i) => (
                <div key={h.symbol} className="pcc-slot-pill pcc-slot-filled"
                  style={{ borderColor: `hsl(${240 + i * 35}, 60%, 55%)40` }}>
                  <span>{h.symbol}</span>
                  <span className="pcc-slot-pct">{h.pct}%</span>
                </div>
              ))}
              {Array.from({ length: budget.slots_remaining }).map((_, i) => (
                <div key={i} className="pcc-slot-pill pcc-slot-empty">空</div>
              ))}
            </div>
          )}
        </div>

        {/* Right: signal queue */}
        <div className="pcc-right">
          {/* Section 1: pending trades */}
          {pendingTrades.length > 0 && (
            <div className="pcc-signal-section">
              <h3 className="pcc-section-title">
                ⏳ 待确认 <span className="pcc-badge">{pendingTrades.length}</span>
              </h3>
              {pendingTrades.map(t => (
                <PendingRow
                  key={t.id}
                  trade={t}
                  onApprove={() => handleApprove(t.id)}
                  onReject={() => handleReject(t.id)}
                />
              ))}
            </div>
          )}

          {/* Section 2: sell signals */}
          {sellSignals.length > 0 && (
            <div className="pcc-signal-section">
              <h3 className="pcc-section-title">
                🔴 卖出信号 <span className="pcc-badge pcc-badge-sell">{sellSignals.length}</span>
              </h3>
              {sellSignals.map(p => (
                <SellSignalRow key={p.symbol} position={p} onRefresh={load} />
              ))}
            </div>
          )}

          {/* Section 3: S&P top 10 candidates */}
          {buySignals.length > 0 && (
            <div className="pcc-signal-section">
              <h3 className="pcc-section-title">
                🔍 S&P 500 扫描 — Top {buySignals.length}
                {data.scan?.scanned_at && (
                  <span className="pcc-scan-time">
                    {new Date(data.scan.scanned_at).toLocaleTimeString()}
                  </span>
                )}
              </h3>
              {buySignals.map((c, i) => (
                <BuySignalRow key={c.symbol} rank={i + 1} candidate={c} budget={budget} />
              ))}
            </div>
          )}

          {data.scan?.status === "not_run" && (
            <div className="brief-empty" style={{ marginTop: 8 }}>
              <p className="brief-empty-text" style={{ fontSize: 12 }}>
                点击「扫描 S&P」获取推荐候选
              </p>
            </div>
          )}

          {pendingTrades.length === 0 && sellSignals.length === 0 && buySignals.length === 0 && (
            <div className="brief-empty" style={{ marginTop: 24 }}>
              <p className="brief-empty-text">暂无信号。点击「运行 Agent」开始分析。</p>
            </div>
          )}
        </div>
      </div>

      {/* ── Calendar heatmap ── */}
      {data.history && data.history.days.length > 0 && (
        <div className="pcc-heatmap-wrap">
          <CalendarHeatmap days={data.history.days} />
        </div>
      )}
    </div>
  );
}

// ── Sub-components ────────────────────────────────────────────────────────────

function HoldingRow({ position: p, onRefresh }: { position: HoldingPosition; onRefresh: () => void }) {
  const [confirming, setConfirming] = useState(false);
  const [loading, setLoading] = useState(false);
  const pl = p.unrealized_pl ?? 0;
  const plPct = p.unrealized_plpc ?? 0;
  const signalColor: Record<string, string> = { SELL: "#ef4444", REDUCE: "#f97316", HOLD: "#22c55e", ADD: "#6366f1" };
  const sig = p.sell_signal ?? "HOLD";

  async function closePos() {
    if (!confirming) { setConfirming(true); return; }
    setLoading(true);
    try { await api.closePosition(p.symbol); setTimeout(onRefresh, 1000); }
    catch (e: unknown) { alert(e instanceof Error ? e.message : "Failed"); }
    finally { setLoading(false); setConfirming(false); }
  }

  return (
    <div className="pcc-holding-row">
      <div className="pcc-holding-main">
        <span className="symbol" style={{ fontSize: 14 }}>{p.symbol}</span>
        <span className="signal-badge" style={{ background: signalColor[sig] ?? "#64748b", fontSize: 11, padding: "1px 6px" }}>{sig}</span>
        <span className={`pcc-pl ${pl >= 0 ? "up" : "down"}`}>
          {pl >= 0 ? "+" : ""}${pl.toFixed(0)} ({plPct >= 0 ? "+" : ""}{plPct.toFixed(1)}%)
        </span>
      </div>
      <div className="pcc-holding-meta">
        <span style={{ color: "var(--muted)", fontSize: 11 }}>${p.current_price?.toFixed(2)} · {p.qty}股</span>
        {sig !== "HOLD" && (
          <>
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
          </>
        )}
      </div>
    </div>
  );
}

function PendingRow({
  trade: t, onApprove, onReject,
}: { trade: PendingTrade; onApprove: () => void; onReject: () => void }) {
  const [confirming, setConfirming] = useState(false);
  const expiresIn = Math.max(0, Math.round((new Date(t.expires_at).getTime() - Date.now()) / 60000));

  return (
    <div className="pcc-signal-row pcc-pending-row">
      <div className="pcc-signal-left">
        <span className={`pcc-side ${t.side === "buy" ? "up" : "down"}`}>
          {t.side === "buy" ? "▲" : "▼"}
        </span>
        <strong>{t.symbol}</strong>
        <span className="signal-badge" style={{ background: t.side === "buy" ? "#16a34a" : "#ef4444", fontSize: 11, padding: "1px 6px" }}>
          {t.signal}
        </span>
        <span style={{ color: "var(--muted)", fontSize: 11 }}>
          {t.notional ? `$${t.notional.toFixed(0)}` : t.qty ? `${t.qty}股` : ""}
        </span>
      </div>
      <div className="pcc-signal-right">
        <span style={{ color: "var(--muted)", fontSize: 11 }}>{expiresIn}m</span>
        {confirming ? (
          <>
            <button className="trade-btn buy-btn" style={{ width: "auto", margin: 0, padding: "4px 12px", background: t.side === "buy" ? "#16a34a" : "#ef4444" }} onClick={onApprove}>
              ✓ 确认
            </button>
            <button className="cancel-small-btn" onClick={() => setConfirming(false)}>✕</button>
          </>
        ) : (
          <>
            <button className="trade-btn buy-btn" style={{ width: "auto", margin: 0, padding: "4px 12px", background: t.side === "buy" ? "#16a34a" : "#ef4444" }}
              onClick={() => setConfirming(true)}>
              Approve
            </button>
            <button className="cancel-small-btn" onClick={onReject}>Reject</button>
          </>
        )}
      </div>
      {t.reason && <p className="pcc-signal-reason">{t.reason}</p>}
    </div>
  );
}

function SellSignalRow({ position: p, onRefresh }: { position: HoldingPosition; onRefresh: () => void }) {
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

function BuySignalRow({ rank, candidate: c, budget }: { rank: number; candidate: ScanCandidate; budget: BudgetAllocation | null }) {
  const [showModal, setShowModal] = useState(false);
  const isBuyable = c.signal === "STRONG_BUY" || c.signal === "BUY";
  const portfolioValue = budget?.portfolio_value ?? 100_000;
  const stop = c.stop_loss ?? (c.price ? c.price * 0.97 : undefined);
  const suggestedNotional = isBuyable && stop && c.price && stop < c.price
    ? Math.min(portfolioValue * 0.02 / (c.price - stop) * c.price, portfolioValue * 0.10)
    : null;

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
        ) : (
          <span style={{ color: "var(--muted)", fontSize: 12 }}>观察</span>
        )}
      </div>
      {c.reason && <p className="pcc-signal-reason" style={{ opacity: isBuyable ? 1 : 0.6 }}>{c.reason}</p>}
    </div>
  );
}

// ── Regime Badge ──────────────────────────────────────────────────────────────

const REGIME_STYLE: Record<string, { bg: string; color: string; icon: string }> = {
  BULL:    { bg: "#16a34a20", color: "#22c55e", icon: "🟢" },
  NEUTRAL: { bg: "#6366f120", color: "#818cf8", icon: "🔵" },
  CAUTION: { bg: "#f59e0b20", color: "#f59e0b", icon: "🟡" },
  BEAR:    { bg: "#ef444420", color: "#ef4444", icon: "🔴" },
};

function RegimeBadge({ regime: r }: { regime: MarketRegime }) {
  const s = REGIME_STYLE[r.regime] ?? REGIME_STYLE.NEUTRAL;
  return (
    <div className="pcc-regime-badge" style={{ background: s.bg, borderColor: s.color + "40" }}
      title={r.reason}>
      <span>{s.icon}</span>
      <div>
        <span style={{ color: s.color, fontWeight: 700, fontSize: 12 }}>{r.regime}</span>
        <span style={{ color: "var(--muted)", fontSize: 10, display: "block" }}>
          SPY {r.spy_change_pct >= 0 ? "+" : ""}{r.spy_change_pct.toFixed(1)}%
          {r.block_buys ? " · 买入已暂停" : ` · ${Math.round(r.size_factor * 100)}% 仓位`}
        </span>
      </div>
    </div>
  );
}

function CircuitBreakerBadge({
  breaker: b, onReset, resetting,
}: { breaker: CircuitBreaker; onReset: () => void; resetting: boolean }) {
  if (!b.triggered) {
    // Show quiet green status when not triggered
    return (
      <div className="pcc-regime-badge" style={{ background: "#16a34a15", borderColor: "#22c55e30" }}
        title={`今日亏损 ${b.daily_loss_pct.toFixed(2)}%，熔断未触发`}>
        <span>🛡️</span>
        <div>
          <span style={{ color: "#22c55e", fontWeight: 700, fontSize: 12 }}>熔断正常</span>
          <span style={{ color: "var(--muted)", fontSize: 10, display: "block" }}>
            今日 {b.daily_loss_pct >= 0 ? "+" : ""}{b.daily_loss_pct.toFixed(2)}%
          </span>
        </div>
      </div>
    );
  }
  return (
    <div className="pcc-regime-badge" style={{ background: "#ef444425", borderColor: "#ef444460" }}
      title={b.reason}>
      <span>🚨</span>
      <div>
        <span style={{ color: "#ef4444", fontWeight: 700, fontSize: 12 }}>熔断触发</span>
        <span style={{ color: "var(--muted)", fontSize: 10, display: "block" }}>
          亏损 {b.daily_loss_pct.toFixed(2)}% · 买入已停
        </span>
      </div>
      <button
        onClick={onReset}
        disabled={resetting}
        style={{
          marginLeft: 6, fontSize: 10, padding: "2px 6px",
          background: "#ef444420", border: "1px solid #ef444440",
          color: "#ef4444", borderRadius: 4, cursor: "pointer",
        }}
      >
        {resetting ? "…" : "重置"}
      </button>
    </div>
  );
}
