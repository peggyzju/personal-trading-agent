import { useState } from "react";
import { api } from "../api/client";
import type { Position } from "../api/client";

export function PositionsTable({ positions, onRefresh }: { positions: Position[]; onRefresh?: () => void }) {
  if (positions.length === 0) {
    return (
      <div className="empty-positions">
        暂无持仓。运行 Agent 并批准交易后在此查看仓位。
      </div>
    );
  }

  return (
    <div className="positions-table-wrap">
      <table className="positions-table">
        <thead>
          <tr>
            <th>股票</th>
            <th>数量</th>
            <th>持仓成本</th>
            <th>现价</th>
            <th>市值</th>
            <th>未实现盈亏</th>
            <th>涨跌幅</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          {positions.map((p) => (
            <PositionRow key={p.symbol} position={p} onRefresh={onRefresh} />
          ))}
        </tbody>
      </table>
    </div>
  );
}

function PositionRow({ position: p, onRefresh }: { position: Position; onRefresh?: () => void }) {
  const [confirming, setConfirming] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleClose() {
    if (!confirming) { setConfirming(true); return; }
    setLoading(true);
    setError(null);
    try {
      await api.closePosition(p.symbol);
      setConfirming(false);
      setTimeout(() => onRefresh?.(), 1000);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed");
    } finally {
      setLoading(false);
    }
  }

  return (
    <tr>
      <td className="symbol">{p.symbol}</td>
      <td>{p.qty}</td>
      <td>${p.avg_entry_price.toFixed(2)}</td>
      <td>${p.current_price.toFixed(2)}</td>
      <td>${p.market_value.toFixed(2)}</td>
      <td className={p.unrealized_pl >= 0 ? "up" : "down"}>
        {p.unrealized_pl >= 0 ? "+" : ""}${p.unrealized_pl.toFixed(2)}
      </td>
      <td className={p.unrealized_plpc >= 0 ? "up" : "down"}>
        {p.unrealized_plpc >= 0 ? "+" : ""}{p.unrealized_plpc.toFixed(2)}%
      </td>
      <td>
        {error && <span className="error-text" style={{ fontSize: 11 }}>{error}</span>}
        <button
          className={`trade-btn ${confirming ? "sell-btn-confirm" : "sell-btn"}`}
          onClick={handleClose}
          disabled={loading}
        >
          {loading ? "…" : confirming ? "确认平仓" : "平仓"}
        </button>
        {confirming && !loading && (
          <button className="cancel-small-btn" onClick={() => setConfirming(false)}>✕</button>
        )}
      </td>
    </tr>
  );
}
