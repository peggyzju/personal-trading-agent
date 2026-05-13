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
    return <div className="brief-offline">启动后端服务以查看仓位分配。</div>;
  }

  if (loading && !data) {
    return <div className="brief-loading">加载仓位分配中…</div>;
  }

  if (!data) {
    return (
      <div className="brief-empty">
        <p className="brief-empty-text">暂无分配数据。</p>
        <button className="brief-generate-btn" onClick={load}>加载分配</button>
      </div>
    );
  }

  const cashBar = data.cash_pct;
  const investedBar = data.invested_pct;

  return (
    <div className="budget-container">
      <div className="scan-header">
        <div>
          <h2>💰 资金分配 &amp; 仓位规模</h2>
          <span className="scan-meta">
            {data.slots_remaining} 个空仓位 ·
            {" "}每笔风险 {data.risk_per_trade_pct}% · 单仓上限 {data.max_position_pct}%
          </span>
        </div>
        <button className="brief-regenerate-btn" onClick={load} disabled={loading}>
          {loading ? "加载中…" : "↺ 刷新"}
        </button>
      </div>

      {/* Overview bar */}
      <div className="budget-overview">
        <div className="budget-stat-row">
          <div className="budget-stat">
            <span className="holding-label">总资产</span>
            <span className="holding-val">${data.portfolio_value.toLocaleString()}</span>
          </div>
          <div className="budget-stat">
            <span className="holding-label">现金</span>
            <span className="holding-val up">${data.cash.toLocaleString()} ({data.cash_pct}%)</span>
          </div>
          <div className="budget-stat">
            <span className="holding-label">已投资</span>
            <span className="holding-val">${data.invested.toLocaleString()} ({data.invested_pct}%)</span>
          </div>
          {data.total_suggested_cost > 0 && (
            <div className="budget-stat">
              <span className="holding-label">建议投入</span>
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
          <span><span className="legend-dot invested" /> 已投资 {investedBar}%</span>
          <span><span className="legend-dot cash" /> 现金 {cashBar}%</span>
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
            <h3>建议买入 — 仓位规模</h3>
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
                    <span className="holding-label">股数</span>
                    <span className="holding-val">{b.shares}</span>
                  </div>
                  <div className="holding-price-col">
                    <span className="holding-label">总成本</span>
                    <span className="holding-val">${b.cost.toLocaleString()}</span>
                  </div>
                  <div className="holding-price-col">
                    <span className="holding-label">占比</span>
                    <span className="holding-val">{b.portfolio_pct}%</span>
                  </div>
                  <div className="holding-price-col">
                    <span className="holding-label">最大亏损</span>
                    <span className="holding-val down">${b.max_loss.toLocaleString()}</span>
                  </div>
                </div>
                <div className="budget-levels">
                  <span className="level-stat">
                    <span className="level-label">买入价</span>
                    <span className="level-value">${b.price?.toFixed(2)}</span>
                  </span>
                  <span className="level-stat">
                    <span className="level-label">止损</span>
                    <span className="level-value" style={{ color: "#ef4444" }}>${b.stop_loss?.toFixed(2)}</span>
                  </span>
                  {b.target_price > 0 && (
                    <span className="level-stat">
                      <span className="level-label">目标价</span>
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
            <h3>建议买入</h3>
            <p className="brief-empty-text" style={{ padding: "20px 0" }}>
              先运行 S&amp;P 500 扫描，获取带仓位规模的买入候选股。
            </p>
          </div>
        )}
      </div>
    </div>
  );
}
