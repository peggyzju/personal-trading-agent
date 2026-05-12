import { Order } from "../api/client";

export function OrdersTable({ orders }: { orders: Order[] }) {
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
          </tr>
        </thead>
        <tbody>
          {orders.map((o) => (
            <tr key={o.id}>
              <td className="symbol">{o.symbol}</td>
              <td className={o.side === "buy" ? "up" : "down"}>{o.side.toUpperCase()}</td>
              <td>{o.qty}</td>
              <td>{o.filled_qty}</td>
              <td>{o.filled_avg_price ? `$${o.filled_avg_price.toFixed(2)}` : "—"}</td>
              <td>
                <span className={`order-status order-status-${o.status}`}>{o.status}</span>
              </td>
              <td>{new Date(o.created_at).toLocaleString()}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
