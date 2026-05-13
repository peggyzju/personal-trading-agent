import { useState } from "react";
import { api } from "../api/client";
import type { Order } from "../api/client";

const CANCELLABLE = new Set(["new", "partially_filled", "held", "accepted", "pending_new"]);

export function OrdersTable({ orders, onRefresh }: { orders: Order[]; onRefresh?: () => void }) {
  if (orders.length === 0) return null;

  return (
    <div className="positions-table-wrap">
      <table className="positions-table">
        <thead>
          <tr>
            <th>Symbol</th>
            <th>Side</th>
            <th>Qty</th>
            <th>Filled</th>
            <th>Avg Price</th>
            <th>Status</th>
            <th>Time</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          {orders.map((o) => (
            <OrderRow key={o.id} order={o} onRefresh={onRefresh} />
          ))}
        </tbody>
      </table>
    </div>
  );
}

function OrderRow({ order: o, onRefresh }: { order: Order; onRefresh?: () => void }) {
  const [loading, setLoading] = useState(false);
  const [cancelled, setCancelled] = useState(false);

  async function handleCancel() {
    setLoading(true);
    try {
      await api.cancelOrder(o.id);
      setCancelled(true);
      setTimeout(() => onRefresh?.(), 1000);
    } catch {
      // order may already be filled — just refresh
      onRefresh?.();
    } finally {
      setLoading(false);
    }
  }

  return (
    <tr style={cancelled ? { opacity: 0.4 } : undefined}>
      <td className="symbol">{o.symbol}</td>
      <td className={o.side === "buy" ? "up" : "down"}>{o.side.toUpperCase()}</td>
      <td>{o.qty}</td>
      <td>{o.filled_qty}</td>
      <td>{o.filled_avg_price ? `$${o.filled_avg_price.toFixed(2)}` : "—"}</td>
      <td>
        <span className={`order-status order-status-${o.status}`}>{o.status}</span>
      </td>
      <td>{new Date(o.created_at).toLocaleString()}</td>
      <td>
        {CANCELLABLE.has(o.status) && !cancelled && (
          <button className="cancel-small-btn" onClick={handleCancel} disabled={loading}>
            {loading ? "…" : "✕ Cancel"}
          </button>
        )}
      </td>
    </tr>
  );
}
