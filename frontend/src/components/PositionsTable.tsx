import { Position } from "../api/client";

export function PositionsTable({ positions }: { positions: Position[] }) {
  if (positions.length === 0) {
    return (
      <div className="empty-positions">
        No open positions. Run an analysis and place a paper trade to see them here.
      </div>
    );
  }

  return (
    <div className="positions-table-wrap">
      <table className="positions-table">
        <thead>
          <tr>
            <th>Symbol</th>
            <th>Qty</th>
            <th>Avg Cost</th>
            <th>Current</th>
            <th>Market Value</th>
            <th>Unrealized P&L</th>
            <th>% Change</th>
          </tr>
        </thead>
        <tbody>
          {positions.map((p) => (
            <tr key={p.symbol}>
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
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
