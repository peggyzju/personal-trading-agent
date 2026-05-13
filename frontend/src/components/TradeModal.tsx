import { useState } from "react";
import { api } from "../api/client";

interface Props {
  symbol: string;
  side: "buy" | "sell";
  suggestedPrice?: number;
  stopLoss?: number;
  targetPrice?: number;
  onClose: () => void;
  onSuccess?: () => void;
}

type InputMode = "shares" | "dollars";

export function TradeModal({ symbol, side, suggestedPrice, stopLoss, targetPrice, onClose, onSuccess }: Props) {
  const [mode, setMode] = useState<InputMode>("dollars");
  const [amount, setAmount] = useState("");
  const [orderType, setOrderType] = useState<"market" | "limit">("market");
  const [limitPrice, setLimitPrice] = useState(suggestedPrice ? String(suggestedPrice.toFixed(2)) : "");
  const [submitting, setSubmitting] = useState(false);
  const [result, setResult] = useState<{ success: boolean; message: string } | null>(null);

  const numAmount = parseFloat(amount) || 0;
  const estimatedShares = mode === "dollars" && suggestedPrice
    ? (numAmount / suggestedPrice).toFixed(2)
    : null;
  const estimatedCost = mode === "shares" && suggestedPrice
    ? (numAmount * suggestedPrice).toFixed(2)
    : null;
  const riskAmount = mode === "dollars" && stopLoss && suggestedPrice && numAmount > 0
    ? ((1 - stopLoss / suggestedPrice) * numAmount).toFixed(2)
    : null;

  async function handleSubmit() {
    if (!numAmount || numAmount <= 0) return;
    setSubmitting(true);
    setResult(null);
    try {
      const req = {
        symbol,
        side,
        order_type: orderType,
        ...(mode === "dollars" ? { notional: numAmount } : { qty: numAmount }),
        ...(orderType === "limit" ? { limit_price: parseFloat(limitPrice) } : {}),
      };
      const order = await api.placeTrade(req);
      setResult({ success: true, message: `Order submitted — ID: ${order.id} · Status: ${order.status}` });
      onSuccess?.();
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : "Order failed";
      setResult({ success: false, message: msg });
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="modal-overlay" onClick={(e) => e.target === e.currentTarget && onClose()}>
      <div className="modal">
        <div className="modal-header">
          <h3>
            <span className={side === "buy" ? "up" : "down"}>
              {side === "buy" ? "▲ Buy" : "▼ Sell"}
            </span>{" "}
            {symbol}
          </h3>
          <button className="modal-close" onClick={onClose}>✕</button>
        </div>

        {suggestedPrice && (
          <div className="modal-price-row">
            <span className="modal-label">Market Price</span>
            <span className="modal-value">${suggestedPrice.toFixed(2)}</span>
            {stopLoss && <><span className="modal-label">Stop Loss</span><span className="modal-value" style={{ color: "#ef4444" }}>${stopLoss.toFixed(2)}</span></>}
            {targetPrice && <><span className="modal-label">Target</span><span className="modal-value" style={{ color: "#22c55e" }}>${targetPrice.toFixed(2)}</span></>}
          </div>
        )}

        <div className="modal-section">
          <div className="modal-toggle">
            <button className={`toggle-btn ${mode === "dollars" ? "active" : ""}`} onClick={() => setMode("dollars")}>$ Dollars</button>
            <button className={`toggle-btn ${mode === "shares" ? "active" : ""}`} onClick={() => setMode("shares")}># Shares</button>
          </div>

          <label className="config-label" style={{ marginTop: 12 }}>
            {mode === "dollars" ? "Amount ($)" : "Shares"}
            <input
              className="config-input"
              type="number"
              min={0}
              step={mode === "dollars" ? 100 : 1}
              placeholder={mode === "dollars" ? "e.g. 1000" : "e.g. 5"}
              value={amount}
              onChange={(e) => setAmount(e.target.value)}
              autoFocus
            />
          </label>

          {estimatedShares && numAmount > 0 && (
            <p className="modal-estimate">≈ {estimatedShares} shares</p>
          )}
          {estimatedCost && numAmount > 0 && (
            <p className="modal-estimate">≈ ${estimatedCost} total</p>
          )}
          {riskAmount && numAmount > 0 && (
            <p className="modal-estimate" style={{ color: "#ef4444" }}>Max risk (to stop): −${riskAmount}</p>
          )}
        </div>

        <div className="modal-section">
          <div className="modal-toggle">
            <button className={`toggle-btn ${orderType === "market" ? "active" : ""}`} onClick={() => setOrderType("market")}>Market</button>
            <button className={`toggle-btn ${orderType === "limit" ? "active" : ""}`} onClick={() => setOrderType("limit")}>Limit</button>
          </div>

          {orderType === "limit" && (
            <label className="config-label" style={{ marginTop: 12 }}>
              Limit Price ($)
              <input
                className="config-input"
                type="number"
                step={0.01}
                value={limitPrice}
                onChange={(e) => setLimitPrice(e.target.value)}
              />
            </label>
          )}
        </div>

        {result && (
          <div className={`modal-result ${result.success ? "modal-result-ok" : "modal-result-err"}`}>
            {result.message}
          </div>
        )}

        <div className="modal-actions">
          <button className="brief-generate-btn" style={{ flex: 1 }} onClick={handleSubmit} disabled={submitting || !numAmount}>
            {submitting ? "Submitting…" : `Submit ${side === "buy" ? "Buy" : "Sell"} Order`}
          </button>
          <button className="brief-regenerate-btn" onClick={onClose}>Cancel</button>
        </div>
      </div>
    </div>
  );
}
