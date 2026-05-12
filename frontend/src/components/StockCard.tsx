import { useState } from "react";
import { api } from "../api/client";
import type { Quote, Analysis } from "../api/client";
import { AnalysisPanel } from "./AnalysisPanel";
import { NewsPanel } from "./NewsPanel";

const SENTIMENT_COLOR = { BULLISH: "#22c55e", BEARISH: "#ef4444", NEUTRAL: "#f59e0b" };

interface Props {
  quote: Quote;
  onAnalysisUpdate: (a: Analysis) => void;
  onRemove: (symbol: string) => void;
  backendOnline: boolean;
}

export function StockCard({ quote, onAnalysisUpdate, onRemove, backendOnline }: Props) {
  const [loading, setLoading] = useState(false);
  const analysis = quote.analysis;

  const signalColor = (s?: string) =>
    s === "BUY" ? "#22c55e" : s === "SELL" ? "#ef4444" : "#f59e0b";

  async function runAnalysis() {
    setLoading(true);
    try {
      const result = await api.analyze(quote.symbol);
      onAnalysisUpdate(result);
    } catch (e) {
      console.error(e);
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="stock-card">
      <div className="card-header">
        <div>
          <span className="symbol">{quote.symbol}</span>
          {analysis && (
            <span className="signal-badge" style={{ background: signalColor(analysis.signal) }}>
              {analysis.signal}
            </span>
          )}
          {quote.news_sentiment && !analysis && (
            <span className="signal-badge" style={{ background: SENTIMENT_COLOR[quote.news_sentiment] }}>
              {quote.news_sentiment}
            </span>
          )}
        </div>
        <button className="remove-btn" onClick={() => onRemove(quote.symbol)} title="Remove">✕</button>
      </div>

      {quote.error ? (
        <p className="error-text">{quote.error}</p>
      ) : (
        <>
          <div className="price-row">
            <span className="price">${quote.price?.toFixed(2)}</span>
            <span className={`change ${quote.change_pct >= 0 ? "up" : "down"}`}>
              {quote.change_pct >= 0 ? "▲" : "▼"} {Math.abs(quote.change_pct).toFixed(2)}%
            </span>
          </div>

          {analysis && (
            <div className="confidence-row">
              <span>Confidence</span>
              <div className="confidence-bar">
                <div className="confidence-fill" style={{
                  width: `${analysis.confidence * 100}%`,
                  background: signalColor(analysis.signal),
                }} />
              </div>
              <span>{(analysis.confidence * 100).toFixed(0)}%</span>
            </div>
          )}

          <div className="card-actions">
            <button className="analyze-btn" onClick={runAnalysis} disabled={loading || !backendOnline}>
              {loading ? "Analyzing…" : analysis ? "Re-analyze" : "Analyze with AI"}
            </button>
          </div>

          {analysis && <AnalysisPanel analysis={analysis} />}

          <NewsPanel symbol={quote.symbol} backendOnline={backendOnline} />
        </>
      )}
    </div>
  );
}
