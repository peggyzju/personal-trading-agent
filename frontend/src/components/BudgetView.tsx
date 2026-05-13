import { useState, useEffect } from "react";
import { api } from "../api/client";
import type { BudgetAllocation } from "../api/client";

interface Props { backendOnline: boolean }

export function BudgetView({ backendOnline }: Props) {
  const [data, setData] = useState<BudgetAllocation | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (backendOnline) load();
  }, [backendOnline]);

  async function load() {
    setLoading(true);
    try {
      const result = await api.getBudget();
      setData(result);
    } catch { /* empty */ } finally {
      setLoading(false);
    }
  }

  if (!backendOnline) {
    return <div className="brief-offline">Start the backend to view budget allocation.</div>;
  }

  if (loading && !data) {
    return <div className="brief-loading">Loading allocation…</div>;
  }

  if (!data) {
    return (
      <div className="brief-empty">
        <p className="brief-empty-text">No allocation data yet.</p>
        <button className="brief-generate-btn" onClick={load}>Load Budget</button>
      </div>
    );
  }

  const cashBar = data.cash_pct;
  const investedBar = data.invested_pct;

  return (
    <div className="budget-container">
      <div className="scan-header">
        <div>
          <h2>💰 Budget &amp; Position Sizing</h2>
          <span className="scan-meta">
            {data.slots_remaining} open slot{data.slots_remaining !== 1 ? "s" : ""} ·
            {" "}Risk {data.risk_per_trade_pct}%/trade · Max {data.max_position_pct}%/position
          </span>
        </div>
        <button className="brief-regenerate-btn" onClick={load} disabled={loading}>
          {loading ? "Loading…" : "↺ Refresh"}
        </button>
      </div>

      {/* Overview bar */}
      <div className="budget-overview">
        <div className="budget-stat-row">
          <div className="budget-stat">
            <span className="holding-label">Portfolio</span>
            <span className="holding-val">${data.portfolio_value.toLocaleString()}</span>
          </div>
          <div className="budget-stat">
            <span className="holding-label">Cash</span>
            <span className="holding-val up">${data.cash.toLocaleString()} ({data.cash_pct}%)</span>
          </div>
          <div className="budget-stat">
            <span className="holding-label">Invested</span>
            <span className="holding-val">${data.invested.toLocaleString()} ({data.invested_pct}%)</span>
          </div>
          {data.total_suggested_cost > 0 && (
            <div className="budget-stat">
              <span className="holding-label">Suggested Deploy</span>
              <span className="holding-val" style={{ color: "#f59e0b" }}>
                ${data.total_suggested_cost.toLocaleString()}
              </span>
            </div>
          )}
        </div>

        <div className="allocation-bar">
          <div className="allocation-fill invested" style={{ width: `${investedBar}%` }} title={`Invested ${investedBar}%`} />
          <div className="allocation-fill cash" style={{ width: `${cashBar}%` }} title={`Cash ${cashBar}%`} />
        </div>
        <div className="allocation-legend">
          <span><span className="legend-dot invested" /> Invested {investedBar}%</span>
          <span><span className="legend-dot cash" /> Cash {cashBar}%</span>
        </div>
      </div>

      <div className="budget-grid">
        {/* Compact allocation breakdown — no P&L, that belongs in Holdings tab */}
        {data.holdings.length > 0 && (
          <div className="budget-section">
            <h3>已投资分布</h3>
            <div className="budget-slots-row">
              {data.holdings.map((h) => (
                <div key={h.symbol} className="budget-slot-chip">
                  <span className="symbol" style={{ fontSize: 13 }}>{h.symbol}</span>
                  <span className="budget-pct">{h.pct}%</span>
                </div>
              ))}
              {Array.from({ length: data.slots_remaining }).map((_, i) => (
                <div key={`empty-${i}`} className="budget-slot-chip budget-slot-empty">
                  <span style={{ color: "var(--muted)", fontSize: 12 }}>空仓位</span>
                </div>
              ))}
            </div>
            <p style={{ fontSize: 12, color: "var(--muted)", marginTop: 8 }}>
              持仓详情 & 卖出信号 → 查看 📉 Holdings 标签
            </p>
          </div>
        )}

        {/* Suggested buys */}
        {data.suggested_buys.length > 0 && (
          <div className="budget-section">
            <h3>Suggested Buys — Position Sizing</h3>
            {data.suggested_buys.map((b) => (
              <div key={b.symbol} className="budget-buy-card">
                <div className="candidate-header">
                  <span className="symbol">{b.symbol}</span>
                  <span className="signal-badge" style={{ background: b.signal === "STRONG_BUY" ? "#16a34a" : "#22c55e" }}>
                    {b.signal?.replace("_", " ")}
                  </span>
                  <span className="candidate-score">AI {b.ai_score}/10</span>
                </div>
                <div className="budget-sizing-row">
                  <div className="holding-price-col">
                    <span className="holding-label">Shares</span>
                    <span className="holding-val">{b.shares}</span>
                  </div>
                  <div className="holding-price-col">
                    <span className="holding-label">Cost</span>
                    <span className="holding-val">${b.cost.toLocaleString()}</span>
                  </div>
                  <div className="holding-price-col">
                    <span className="holding-label">% of Portfolio</span>
                    <span className="holding-val">{b.portfolio_pct}%</span>
                  </div>
                  <div className="holding-price-col">
                    <span className="holding-label">Max Loss</span>
                    <span className="holding-val down">${b.max_loss.toLocaleString()}</span>
                  </div>
                </div>
                <div className="budget-levels">
                  <span className="level-stat">
                    <span className="level-label">Entry</span>
                    <span className="level-value">${b.price?.toFixed(2)}</span>
                  </span>
                  <span className="level-stat">
                    <span className="level-label">Stop</span>
                    <span className="level-value" style={{ color: "#ef4444" }}>${b.stop_loss?.toFixed(2)}</span>
                  </span>
                  {b.target_price > 0 && (
                    <span className="level-stat">
                      <span className="level-label">Target</span>
                      <span className="level-value" style={{ color: "#22c55e" }}>${b.target_price?.toFixed(2)}</span>
                    </span>
                  )}
                </div>
                {b.reason && <p className="candidate-reason">{b.reason}</p>}
              </div>
            ))}
          </div>
        )}

        {data.suggested_buys.length === 0 && data.slots_remaining > 0 && (
          <div className="budget-section">
            <h3>Suggested Buys</h3>
            <p className="brief-empty-text" style={{ padding: "20px 0" }}>
              Run the S&P 500 scan first to get buy candidates with position sizing.
            </p>
          </div>
        )}
      </div>
    </div>
  );
}
