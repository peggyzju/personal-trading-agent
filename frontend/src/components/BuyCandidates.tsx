import { useState, useEffect } from "react";
import { api } from "../api/client";
import type { ScanResult, ScanCandidate } from "../api/client";
import { TradeModal } from "./TradeModal";

const SIGNAL_COLOR: Record<string, string> = {
  STRONG_BUY: "#16a34a",
  BUY: "#22c55e",
  HOLD: "#f59e0b",
  SELL: "#ef4444",
};

const SIGNAL_LABEL: Record<string, string> = {
  STRONG_BUY: "STRONG BUY",
  BUY: "BUY",
  HOLD: "HOLD",
  SELL: "SELL",
};

interface Props {
  backendOnline: boolean;
}

export function BuyCandidates({ backendOnline }: Props) {
  const [scan, setScan] = useState<ScanResult | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (backendOnline) loadScan();
  }, [backendOnline]);

  async function loadScan() {
    try {
      const result = await api.getScan();
      setScan(result);
    } catch { /* no scan yet */ }
  }

  async function triggerScan() {
    setLoading(true);
    setScan({ status: "running", candidates: [] });
    try {
      await api.triggerScan();
      // poll until done
      const poll = setInterval(async () => {
        const result = await api.getScan();
        setScan(result);
        if (result.status !== "running") {
          clearInterval(poll);
          setLoading(false);
        }
      }, 3000);
    } catch {
      setLoading(false);
    }
  }

  if (!backendOnline) {
    return <div className="brief-offline">Start the backend to run the S&P 500 scanner.</div>;
  }

  if (!scan || scan.status === "not_run") {
    return (
      <div className="brief-empty">
        <p className="brief-empty-text">No scan run today yet.</p>
        <button className="brief-generate-btn" onClick={triggerScan} disabled={loading}>
          🔍 Scan S&P 500 Now
        </button>
        <p className="brief-disclaimer">Screens ~500 stocks then runs AI scoring — takes ~60s</p>
      </div>
    );
  }

  if (scan.status === "running") {
    return (
      <div className="scan-running">
        <div className="scan-spinner" />
        <p>Scanning S&P 500… fetching prices, computing signals, AI scoring top candidates.</p>
        <p className="brief-disclaimer">This takes about 60 seconds</p>
      </div>
    );
  }

  if (scan.status === "error") {
    return (
      <div className="brief-empty">
        <p className="error-text">Scan failed. Check that yfinance can reach the internet.</p>
        <button className="brief-generate-btn" onClick={triggerScan}>Retry</button>
      </div>
    );
  }

  return (
    <div className="candidates-container">
      <div className="scan-header">
        <div>
          <h2>🔍 S&P 500 Scan — AI Ratings</h2>
          <span className="scan-meta">
            {scan.total_screened} screened → {scan.tech_passed} passed technical filter →
            {" "}{scan.candidates.length} AI-scored
          </span>
          {scan.scanned_at && (
            <span className="scan-meta"> · {new Date(scan.scanned_at + "Z").toLocaleTimeString()}</span>
          )}
        </div>
        <button className="brief-regenerate-btn" onClick={triggerScan} disabled={loading}>
          ↺ Re-scan
        </button>
      </div>

      <div className="candidates-grid">
        {scan.candidates.map((c, i) => (
          <CandidateCard key={c.symbol} rank={i + 1} candidate={c} />
        ))}
      </div>
    </div>
  );
}

function CandidateCard({ rank, candidate: c }: { rank: number; candidate: ScanCandidate }) {
  const signalColor = SIGNAL_COLOR[c.signal] ?? "#f59e0b";
  const [showModal, setShowModal] = useState(false);
  const canBuy = c.signal === "STRONG_BUY" || c.signal === "BUY";

  return (
    <div className="candidate-card">
      {showModal && (
        <TradeModal
          symbol={c.symbol}
          side="buy"
          suggestedPrice={c.price}
          stopLoss={c.stop_loss}
          targetPrice={c.target_price}
          onClose={() => setShowModal(false)}
        />
      )}
      <div className="candidate-header">
        <span className="candidate-rank">#{rank}</span>
        <span className="symbol">{c.symbol}</span>
        <span className="signal-badge" style={{ background: signalColor }}>{SIGNAL_LABEL[c.signal] ?? c.signal?.replace("_", " ")}</span>
        <span className="candidate-score">AI {c.ai_score}/10</span>
      </div>

      <div className="price-row">
        <span className="price">${c.price?.toFixed(2)}</span>
        <span className={`change ${(c.momentum_5d ?? 0) >= 0 ? "up" : "down"}`}>
          {(c.momentum_5d ?? 0) >= 0 ? "▲" : "▼"} {Math.abs(c.momentum_5d ?? 0).toFixed(1)}% (5d)
        </span>
      </div>

      <div className="candidate-technicals">
        <TechStat label="Volume" value={`${c.volume_ratio?.toFixed(1)}x avg`} />
        <TechStat label="RSI" value={c.rsi?.toString() ?? "—"} />
        <TechStat label="Breakout" value={c.near_breakout ? "✓ Yes" : "No"} highlight={c.near_breakout} />
        <TechStat label="Timeframe" value={c.timeframe?.replace(/_/g, " ") ?? "—"} />
      </div>

      <p className="candidate-reason">{c.reason}</p>

      <div className="candidate-levels">
        <LevelStat label="Entry" value={c.entry_note ?? "at market"} />
        <LevelStat label="Stop" value={c.stop_loss ? `$${c.stop_loss.toFixed(2)}` : "—"} color="#ef4444" />
        <LevelStat label="Target" value={c.target_price ? `$${c.target_price.toFixed(2)}` : "—"} color="#22c55e" />
      </div>

      {canBuy && (
        <button className="trade-btn buy-btn" onClick={() => setShowModal(true)}>
          ＋ Buy {c.symbol}
        </button>
      )}
    </div>
  );
}

function TechStat({ label, value, highlight }: { label: string; value: string; highlight?: boolean }) {
  return (
    <div className="tech-stat">
      <span className="tech-label">{label}</span>
      <span className="tech-value" style={highlight ? { color: "#22c55e" } : undefined}>{value}</span>
    </div>
  );
}

function LevelStat({ label, value, color }: { label: string; value: string; color?: string }) {
  return (
    <div className="level-stat">
      <span className="level-label">{label}</span>
      <span className="level-value" style={color ? { color } : undefined}>{value}</span>
    </div>
  );
}
