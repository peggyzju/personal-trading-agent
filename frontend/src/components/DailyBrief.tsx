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
  const [hasCache, setHasCache] = useState<boolean | null>(null); // null = not checked yet

  async function checkCache() {
    if (!backendOnline) return;
    try {
      const result = await api.getBrief();
      if ((result as any).status === "running") {
        setLoading(true);
        pollUntilDone();
      } else {
        setBrief(result);
        setHasCache(true);
      }
    } catch {
      setHasCache(false); // 404 = no cache
    }
  }

  function pollUntilDone() {
    const poll = setInterval(async () => {
      try {
        const result = await api.getBrief();
        if ((result as any).status === "running") return;
        clearInterval(poll);
        setLoading(false);
        if ((result as any).status === "error") {
          setHasCache(false);
        } else {
          setBrief(result);
          setHasCache(true);
        }
      } catch {
        clearInterval(poll);
        setLoading(false);
        setHasCache(false);
      }
    }, 3000);
    setTimeout(() => { clearInterval(poll); setLoading(false); }, 60_000);
  }

  async function generate() {
    setLoading(true);
    setBrief(null);
    try {
      await api.generateBrief();
      pollUntilDone();
    } catch {
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

  // First visit — haven't checked cache yet
  if (hasCache === null && !loading && !brief) {
    return (
      <div className="brief-empty">
        <button className="brief-generate-btn" onClick={checkCache}>
          Load Today's Brief
        </button>
      </div>
    );
  }

  // No cache — show generate button
  if (hasCache === false && !loading) {
    return (
      <div className="brief-empty">
        <p className="brief-empty-text">No brief generated yet for today.</p>
        <button className="brief-generate-btn" onClick={generate}>
          ✨ Generate Daily Brief
        </button>
        <p className="brief-disclaimer">Takes ~15–20s — Claude reads all watchlist news + prices</p>
      </div>
    );
  }

  // Loading / generating
  if (loading) {
    return (
      <div className="scan-running">
        <div className="scan-spinner" />
        <p>Claude is reading the news and generating your brief…</p>
        <p className="brief-disclaimer">Usually takes 15–20 seconds</p>
      </div>
    );
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
        <button className="brief-regenerate-btn" onClick={generate} disabled={loading}>
          ↺ Refresh
        </button>
      </div>

      <p className="brief-summary">{brief.sentiment_summary}</p>

      <div className="brief-grid">
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
