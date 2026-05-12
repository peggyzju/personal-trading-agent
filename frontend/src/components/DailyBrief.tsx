import { useState } from "react";
import { api } from "../api/client";
import type { DailyBrief as DailyBriefType } from "../api/client";

const MOOD_COLOR = { RISK_ON: "#22c55e", RISK_OFF: "#ef4444", MIXED: "#f59e0b" };
const IMPACT_COLOR = { BULLISH: "#22c55e", BEARISH: "#ef4444", NEUTRAL: "#f59e0b" };
const ACTION_COLOR = { BUY: "#22c55e", SELL: "#ef4444", WATCH: "#f59e0b" };

interface Props {
  backendOnline: boolean;
}

export function DailyBrief({ backendOnline }: Props) {
  const [brief, setBrief] = useState<DailyBriefType | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  async function load(generate = false) {
    if (!backendOnline) return;
    setLoading(true);
    setError("");
    try {
      const result = generate ? await api.generateBrief() : await api.getBrief();
      setBrief(result);
    } catch {
      if (!generate) {
        // no cached brief — prompt to generate
        setError("no_cache");
      } else {
        setError("Failed to generate brief.");
      }
    } finally {
      setLoading(false);
    }
  }

  if (!backendOnline) {
    return (
      <div className="brief-offline">
        Start the backend (<code>python main.py</code>) to generate a daily market brief.
      </div>
    );
  }

  if (!brief && !loading && error !== "no_cache") {
    return (
      <div className="brief-empty">
        <button className="brief-generate-btn" onClick={() => load(false)}>
          Load Today's Brief
        </button>
      </div>
    );
  }

  if (error === "no_cache") {
    return (
      <div className="brief-empty">
        <p className="brief-empty-text">No brief generated yet for today.</p>
        <button className="brief-generate-btn" onClick={() => load(true)} disabled={loading}>
          {loading ? "Generating…" : "✨ Generate Daily Brief"}
        </button>
        <p className="brief-disclaimer">Takes ~15–20s — Claude reads all watchlist news + prices</p>
      </div>
    );
  }

  if (loading) {
    return <div className="brief-loading">Generating market brief… Claude is reading the news.</div>;
  }

  if (!brief) return null;

  return (
    <div className="brief-container">
      <div className="brief-header">
        <div>
          <h2 className="brief-headline">{brief.headline}</h2>
          <span className="mood-badge" style={{ background: MOOD_COLOR[brief.market_mood] }}>
            {brief.market_mood.replace("_", " ")}
          </span>
          <span className="brief-date">Generated {brief.generated_at}</span>
        </div>
        <button className="brief-regenerate-btn" onClick={() => load(true)} disabled={loading}>
          ↺ Refresh
        </button>
      </div>

      <p className="brief-summary">{brief.sentiment_summary}</p>

      <div className="brief-grid">
        {/* Top Movers */}
        <section className="brief-section">
          <h3>📈 Top Movers</h3>
          {brief.top_movers.map((m, i) => (
            <div key={i} className="mover-row">
              <span className="mover-symbol">{m.symbol}</span>
              <span className={`mover-pct ${m.change_pct >= 0 ? "up" : "down"}`}>
                {m.change_pct >= 0 ? "▲" : "▼"} {Math.abs(m.change_pct).toFixed(2)}%
              </span>
              <p className="mover-reason">{m.reason}</p>
            </div>
          ))}
        </section>

        {/* Key Events */}
        <section className="brief-section">
          <h3>📅 Key Events</h3>
          {brief.key_events.map((e, i) => (
            <div key={i} className="event-row">
              <span className="event-badge" style={{ color: IMPACT_COLOR[e.impact] }}>
                {e.impact}
              </span>
              <span className="event-name">{e.event}</span>
              <p className="event-detail">{e.detail}</p>
            </div>
          ))}
        </section>

        {/* Trading Opportunities */}
        <section className="brief-section">
          <h3>💡 Opportunities</h3>
          {brief.trading_opportunities.map((o, i) => (
            <div key={i} className="opportunity-row">
              <span className="opp-symbol">{o.symbol}</span>
              <span className="opp-action" style={{ color: ACTION_COLOR[o.action] }}>
                {o.action}
              </span>
              <p className="opp-rationale">{o.rationale}</p>
            </div>
          ))}
        </section>

        {/* Risks */}
        <section className="brief-section">
          <h3>⚠ Risks to Watch</h3>
          <ul className="risks-list">
            {brief.risks_to_watch.map((r, i) => <li key={i}>{r}</li>)}
          </ul>
        </section>
      </div>
    </div>
  );
}
