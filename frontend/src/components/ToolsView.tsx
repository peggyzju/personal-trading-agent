import { useState } from "react";
import type { Position, Order } from "../api/client";
import { BacktestView } from "./BacktestView";
import { OrdersTable } from "./OrdersTable";
import { PositionsTable } from "./PositionsTable";

interface Props {
  backendOnline: boolean;
  positions: Position[];
  orders: Order[];
  onRefresh: () => void;
}

type ToolTab = "backtest" | "orders";

export function ToolsView({ backendOnline, positions, orders, onRefresh }: Props) {
  const [tab, setTab] = useState<ToolTab>("backtest");

  const tabs: { id: ToolTab; label: string }[] = [
    { id: "backtest", label: "📊 策略回测" },
    { id: "orders",   label: "📋 订单记录" },
  ];

  return (
    <div className="tools-view">
      <nav className="tools-sub-nav">
        {tabs.map(t => (
          <button
            key={t.id}
            className={`tools-sub-tab${tab === t.id ? " active" : ""}`}
            onClick={() => setTab(t.id)}
          >
            {t.label}
          </button>
        ))}
      </nav>

      <div className="tools-content">
        {tab === "backtest" && <BacktestView backendOnline={backendOnline} />}
        {tab === "orders"   && (
          <div>
            <PositionsTable positions={positions} onRefresh={onRefresh} />
            <OrdersTable orders={orders} onRefresh={onRefresh} />
          </div>
        )}
      </div>
    </div>
  );
}
