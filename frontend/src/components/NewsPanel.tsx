import { useState } from "react";
import { api } from "../api/client";
import type { NewsItem, NewsSentiment } from "../api/client";

const SENTIMENT_COLOR = { BULLISH: "#22c55e", BEARISH: "#ef4444", NEUTRAL: "#f59e0b" };
const RELEVANCE_COLOR = { HIGH: "#ef4444", MEDIUM: "#f59e0b", LOW: "#64748b" };

interface Props {
  symbol: string;
  backendOnline: boolean;
}

export function NewsPanel({ symbol, backendOnline }: Props) {
  const [items, setItems] = useState<NewsItem[]>([]);
  const [sentiment, setSentiment] = useState<NewsSentiment | null>(null);
  const [loading, setLoading] = useState(false);
  const [open, setOpen] = useState(false);

  async function loadNews() {
    if (!backendOnline) return;
    setLoading(true);
    try {
      const result = await api.getNews(symbol);
      setItems(result.items);
      setOpen(true);
    } finally {
      setLoading(false);
    }
  }

  async function runSentiment() {
    if (!backendOnline) return;
    setLoading(true);
    try {
      const result = await api.analyzeNewsSentiment(symbol);
      setSentiment(result);
      setItems(result.items);
      setOpen(true);
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="news-panel">
      <div className="news-actions">
        <button className="news-btn" onClick={loadNews} disabled={loading || !backendOnline}>
          {loading ? "Loading…" : "📰 News"}
        </button>
        <button className="news-btn sentiment" onClick={runSentiment} disabled={loading || !backendOnline}>
          {loading ? "…" : "🧠 Sentiment"}
        </button>
      </div>

      {sentiment && (
        <div className="sentiment-summary">
          <span className="sentiment-badge" style={{ background: SENTIMENT_COLOR[sentiment.overall] }}>
            {sentiment.overall}
          </span>
          <p className="insight">{sentiment.key_insight}</p>
          {sentiment.watch_for && (
            <p className="watch-for">👀 Watch: {sentiment.watch_for}</p>
          )}
        </div>
      )}

      {open && items.length > 0 && (
        <div className="news-list">
          {items.map((item, i) => (
            <div key={i} className="news-item">
              <div className="news-item-header">
                {item.relevance && (
                  <span className="news-tag" style={{ color: RELEVANCE_COLOR[item.relevance] }}>
                    {item.relevance}
                  </span>
                )}
                {item.sentiment && (
                  <span className="news-tag" style={{ color: SENTIMENT_COLOR[item.sentiment] }}>
                    {item.sentiment}
                  </span>
                )}
                {item.impact && (
                  <span className="news-tag muted">{item.impact.replace("_", " ")}</span>
                )}
                <span className="news-source">{item.source}</span>
              </div>
              <a className="news-title" href={item.url} target="_blank" rel="noreferrer">
                {item.title}
              </a>
              {item.reason && <p className="news-reason">{item.reason}</p>}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
