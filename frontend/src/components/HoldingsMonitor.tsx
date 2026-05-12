import { useState, useEffect } from "react";
import { api } from "../api/client";
import type { HoldingsResult, HoldingPosition } from "../api/client";

const SELL_COLOR: Record<string, string> = {
  SELL:   "#ef4444",
  REDUCE: "#f97316",
  HOLD:   "#22c55e",
  ADD:    "#6366f1",
};
const URGENCY_COLOR: Record<string, string> = {
  HIGH:   "#ef4444",
  MEDIUM: "#f59e0b",
  LOW:    "#64748b",
};

interface Props { backendOnline: boolean }

export function HoldingsMonitor({ backendOnline }: Props) {
  const [data, setData] = useState<HoldingsResult | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (backendOnline) load();
  }, [backendOnline]);

  async function load() {
    try {
      const result = await api.getHoldings();
      setData(result);
    } catch { /* empty */ }
  }

  async function refresh() {
    setLoading(true);
    try {
      await api.refreshHoldings();
      // poll until positions appear
      const poll = setInterval(async () => {
        const result = await api.getHoldings();
        setData(result);
        if (result.positions.length > 0) {
          clearInterval(poll);
          setLoading(false);
        }
      }, 2000);
      setTimeout(() => { clearInterval(poll); setLoading(false); }, 20000);
    } catch {
      setLoading(false);
    }
  }

  if (!backendOnline) {
    return <div className="brief-offline">Start the backend to monitor holdings.</div>;
  }

  const positions = data?.positions ?? [];

  return (
    <div className="holdings-container">
      <div className="scan-header">
        <div>
          <h2>📉 Holdings Monitor</h2>
          <span className="scan-meta">
            {positions.length} position{positions.length !== 1 ? "s" : ""} · paper trading
            {data?.analyzed && " · sell signals analyzed"}
          </span>
        </div>
        <button className="brief-regenerate-btn" onClick={refresh} disabled={loading}>
          {loading ? "Refreshing…" : "↺ Refresh + Analyze"}
        </button>
      </div>

      {positions.length === 0 ? (
        <div className="brief-empty">
          <p className="brief-empty-text">No positions found.</p>
          <button className="brief-generate-btn" onClick={refresh} disabled={loading}>
            {loading ? "Loading…" : "Load Paper Portfolio"}
          </button>
          <p className="brief-disclaimer">Uses demo paper portfolio if Alpaca keys not configured</p>
        </div>
      ) : (
        <div className="holdings-grid">
          {positions.map((p) => <HoldingCard key={p.symbol} position={p} />)}
        </div>
      )}
    </div>
  );
}

function HoldingCard({ position: p }: { position: HoldingPosition }) {
  const pl = p.unrealized_pl ?? 0;
  const plPct = p.unrealized_plpc ?? 0;
  const signal = p.sell_signal ?? "HOLD";
  const signalColor = SELL_COLOR[signal] ?? "#64748b";
  const urgency = p.urgency ?? "LOW";

  return (
    <div className="holding-card" style={{ borderLeftColor: signalColor }}>
      <div className="candidate-header">
        <span className="symbol">{p.symbol}</span>
        <span className="signal-badge" style={{ background: signalColor }}>{signal}</span>
        {p.urgency && (
          <span className="urgency-badge" style={{ color: URGENCY_COLOR[urgency] }}>
            {urgency}
          </span>
        )}
      </div>

      <div className="holding-prices">
        <div className="holding-price-col">
          <span className="holding-label">Entry</span>
          <span className="holding-val">${p.avg_entry_price?.toFixed(2)}</span>
        </div>
        <div className="holding-price-col">
          <span className="holding-label">Now</span>
          <span className="holding-val">${p.current_price?.toFixed(2)}</span>
        </div>
        <div className="holding-price-col">
          <span className="holding-label">Qty</span>
          <span className="holding-val">{p.qty}</span>
        </div>
        <div className="holding-price-col">
          <span className="holding-label">P&L</span>
          <span className={`holding-val ${pl >= 0 ? "up" : "down"}`}>
            {pl >= 0 ? "+" : ""}${pl.toFixed(0)} ({plPct >= 0 ? "+" : ""}{plPct.toFixed(1)}%)
          </span>
        </div>
      </div>

      {p.reason && <p className="candidate-reason">{p.reason}</p>}
      {p.suggested_action && (
        <div className="suggested-action">
          💡 {p.suggested_action}
        </div>
      )}
    </div>
  );
}
