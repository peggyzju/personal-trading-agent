import type { Position, Order } from "../api/client";
import { OrdersTable } from "./OrdersTable";
import { PositionsTable } from "./PositionsTable";

interface Props {
  backendOnline: boolean;
  positions: Position[];
  orders: Order[];
  onRefresh: () => void;
}

export function ToolsView({ backendOnline: _backendOnline, positions, orders, onRefresh }: Props) {
  return (
    <div className="tools-view">
      <div className="tools-content">
        <PositionsTable positions={positions} onRefresh={onRefresh} />
        <OrdersTable orders={orders} onRefresh={onRefresh} />
      </div>
    </div>
  );
}
