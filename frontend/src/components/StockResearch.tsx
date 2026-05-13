import { useState, useEffect, useCallback, useRef } from "react";
import { api } from "../api/client";
import type { ScanResult, ScanCandidate, Quote, Analysis, NewsSentiment, NewsItem } from "../api/client";
import { AnalysisPanel } from "./AnalysisPanel";

const LS_KEY = "watchlist";

function loadWatchlist(): string[] {
  try {
    const raw = localStorage.getItem(LS_KEY);
    return raw ? JSON.parse(raw) : [];
  } catch { return []; }
}

function saveWatchlist(symbols: string[]) {
  localStorage.setItem(LS_KEY, JSON.stringify(symbols));
}

// One-time migration: wipe stale default data written by the old App.tsx
function clearStaleDefaults() {
  const STALE = ["AAPL", "NVDA", "MSFT", "TSLA", "MOD", "APP", "VRT", "BA", "AMZN"];
  try {
    const raw = localStorage.getItem(LS_KEY);
    if (!raw) return;
    const stored: string[] = JSON.parse(raw);
    // If every stored symbol is one of the old defaults, wipe it
    if (stored.every(s => STALE.includes(s))) {
      localStorage.removeItem(LS_KEY);
    }
  } catch { /* ignore */ }
}

interface Props { backendOnline: boolean }

// ── Signal colors ─────────────────────────────────────────────────────────────

const SIGNAL_BG: Record<string, string> = {
  STRONG_BUY: "#16a34a",
  BUY:        "#22c55e",
  HOLD:       "#64748b",
  SELL:       "#ef4444",
  WATCH:      "#f59e0b",
};

const SENTIMENT_COLOR: Record<string, string> = {
  BULLISH: "#22c55e",
  BEARISH: "#ef4444",
  NEUTRAL: "#f59e0b",
};

// ── Inline sentiment / news panel ─────────────────────────────────────────────

function InlineNewsPanel({ symbol, backendOnline }: { symbol: string; backendOnline: boolean }) {
  const [items, setItems] = useState<NewsItem[]>([]);
  const [sentiment, setSentiment] = useState<NewsSentiment | null>(null);
  const [loading, setLoading] = useState(false);
  const [loaded, setLoaded] = useState(false);

  async function load() {
    if (!backendOnline || loading) return;
    setLoading(true);
    try {
      const result = await api.analyzeNewsSentiment(symbol);
      setSentiment(result);
      setItems(result.items);
      setLoaded(true);
    } finally { setLoading(false); }
  }

  if (!loaded) {
    return (
      <div className="rs-lazy-placeholder">
        <button className="rs-load-btn" onClick={load} disabled={loading || !backendOnline}>
          {loading ? "分析中…" : "🧠 加载舆情分析"}
        </button>
      </div>
    );
  }

  return (
    <div className="rs-section-body">
      {sentiment && (
        <div className="rs-sentiment-row">
          <span className="signal-badge" style={{ background: SENTIMENT_COLOR[sentiment.overall] ?? "#64748b" }}>
            {sentiment.overall}
          </span>
          <p className="rs-insight">{sentiment.key_insight}</p>
          {sentiment.watch_for && (
            <p className="rs-watch-for">👀 {sentiment.watch_for}</p>
          )}
        </div>
      )}
      {items.length > 0 && (
        <div className="rs-news-list">
          {items.slice(0, 5).map((item, i) => (
            <div key={i} className="rs-news-item">
              <div className="rs-news-meta">
                {item.sentiment && (
                  <span className="rs-news-tag" style={{ color: SENTIMENT_COLOR[item.sentiment] ?? "#64748b" }}>
                    {item.sentiment}
                  </span>
                )}
                {item.relevance && (
                  <span className="rs-news-tag" style={{ color: item.relevance === "HIGH" ? "#ef4444" : item.relevance === "MEDIUM" ? "#f59e0b" : "#64748b" }}>
                    {item.relevance}
                  </span>
                )}
                <span className="rs-news-source">{item.source}</span>
              </div>
              <a className="rs-news-title" href={item.url} target="_blank" rel="noreferrer">
                {item.title}
              </a>
              {item.reason && <p className="rs-news-reason">{item.reason}</p>}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ── Inline AI analysis panel ──────────────────────────────────────────────────

interface AIState {
  analysis: Analysis | null;
  loading: boolean;
  loaded: boolean;
  error: string | null;
}

function InlineAIPanel({
  symbol, backendOnline, state, onLoad,
}: {
  symbol: string;
  backendOnline: boolean;
  state: AIState;
  onLoad: () => void;
}) {
  const { analysis, loading, loaded, error } = state;

  if (!loaded) {
    return (
      <div className="rs-lazy-placeholder">
        {error && <p className="rs-error-msg">⚠️ {error} — <button className="rs-retry-link" onClick={onLoad}>重试</button></p>}
        <button className="rs-load-btn" onClick={onLoad} disabled={loading || !backendOnline}>
          {loading ? "🤖 AI 分析中，约需 30–60 秒…" : "🤖 加载 AI 分析"}
        </button>
      </div>
    );
  }

  return (
    <div className="rs-section-body">
      {analysis && <AnalysisPanel analysis={analysis} />}
    </div>
  );
}

// ── Market monitor panel ──────────────────────────────────────────────────────

function MarketMonitorPanel({ data }: {
  data: {
    price?: number;
    change_pct?: number;
    volume?: number;
    volume_ratio?: number;
    rsi?: number;
    momentum_5d?: number;
    stop_loss?: number;
    target_price?: number;
    near_breakout?: boolean;
    timeframe?: string;
  }
}) {
  const stats = [
    data.price !== undefined && { label: "价格", value: `$${data.price.toFixed(2)}`, color: undefined },
    data.change_pct !== undefined && {
      label: "涨跌幅",
      value: `${data.change_pct >= 0 ? "+" : ""}${data.change_pct.toFixed(2)}%`,
      color: data.change_pct >= 0 ? "#22c55e" : "#ef4444",
    },
    data.rsi !== undefined && {
      label: "RSI",
      value: data.rsi.toFixed(1),
      color: data.rsi > 70 ? "#ef4444" : data.rsi < 30 ? "#22c55e" : "#f59e0b",
    },
    data.volume_ratio !== undefined && {
      label: "量比",
      value: `${data.volume_ratio.toFixed(1)}x`,
      color: data.volume_ratio > 1.5 ? "#22c55e" : "var(--muted)",
    },
    data.momentum_5d !== undefined && {
      label: "5日动量",
      value: `${data.momentum_5d >= 0 ? "+" : ""}${data.momentum_5d.toFixed(2)}%`,
      color: data.momentum_5d >= 0 ? "#22c55e" : "#ef4444",
    },
    data.stop_loss !== undefined && {
      label: "止损",
      value: `$${data.stop_loss.toFixed(2)}`,
      color: "#ef4444",
    },
    data.target_price !== undefined && data.target_price > 0 && {
      label: "目标价",
      value: `$${data.target_price.toFixed(2)}`,
      color: "#22c55e",
    },
    data.timeframe && { label: "时间框架", value: data.timeframe, color: undefined },
  ].filter(Boolean) as { label: string; value: string; color: string | undefined }[];

  return (
    <div className="rs-section-body">
      {data.near_breakout && (
        <div className="rs-breakout-badge">🚀 接近突破位</div>
      )}
      <div className="rs-market-stats">
        {stats.map((s, i) => (
          <div key={i} className="rs-mstat">
            <span className="rs-mstat-label">{s.label}</span>
            <span className="rs-mstat-value" style={s.color ? { color: s.color } : undefined}>{s.value}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

// ── Expandable section tab ────────────────────────────────────────────────────

type SectionKey = "market" | "ai" | "sentiment";

function SectionTabs({
  active,
  onChange,
}: {
  active: SectionKey | null;
  onChange: (key: SectionKey | null) => void;
}) {
  const tabs: { key: SectionKey; label: string }[] = [
    { key: "market", label: "📊 行情" },
    { key: "ai", label: "🤖 AI 分析" },
    { key: "sentiment", label: "📰 舆情" },
  ];

  return (
    <div className="rs-tabs">
      {tabs.map(t => (
        <button
          key={t.key}
          className={`rs-tab${active === t.key ? " rs-tab-active" : ""}`}
          onClick={() => onChange(active === t.key ? null : t.key)}
        >
          {t.label}
        </button>
      ))}
    </div>
  );
}

// ── S&P Scan candidate card ───────────────────────────────────────────────────

function ScanCard({
  rank,
  candidate: c,
  backendOnline,
}: {
  rank: number;
  candidate: ScanCandidate;
  backendOnline: boolean;
}) {
  const [section, setSection] = useState<SectionKey | null>(null);
  const [aiState, setAiState] = useState<AIState>({
    analysis: null, loading: false, loaded: false, error: null,
  });
  const isBuyable = c.signal === "STRONG_BUY" || c.signal === "BUY";

  async function loadAI() {
    if (!backendOnline || aiState.loading) return;
    setAiState(s => ({ ...s, loading: true, error: null }));
    try {
      const result = await api.analyze(c.symbol);
      setAiState({ analysis: result, loading: false, loaded: true, error: null });
    } catch (e) {
      const msg = e instanceof Error ? e.message : "分析请求失败";
      setAiState(s => ({ ...s, loading: false, error: msg }));
    }
  }

  return (
    <div className={`rs-card${isBuyable ? " rs-card-buyable" : ""}`}>
      <div className="rs-card-header">
        <div className="rs-card-left">
          <span className="rs-rank">#{rank}</span>
          <span className="symbol" style={{ fontSize: 15, opacity: isBuyable ? 1 : 0.7 }}>{c.symbol}</span>
          <span className="signal-badge" style={{ background: SIGNAL_BG[c.signal] ?? "#64748b", opacity: isBuyable ? 1 : 0.75 }}>
            {c.signal?.replace("_", " ")}
          </span>
          <span className="rs-score">AI {c.ai_score}/10</span>
        </div>
        <div className="rs-card-right">
          {c.price > 0 && (
            <span className="rs-price">${c.price.toFixed(2)}</span>
          )}
          {c.rsi !== undefined && (
            <span className="rs-meta">RSI {c.rsi.toFixed(0)}</span>
          )}
          {c.volume_ratio !== undefined && (
            <span className="rs-meta" style={{ color: c.volume_ratio > 1.5 ? "#22c55e" : "var(--muted)" }}>
              {c.volume_ratio.toFixed(1)}x vol
            </span>
          )}
        </div>
      </div>

      {c.reason && (
        <p className="rs-reason">{c.reason}</p>
      )}

      <SectionTabs active={section} onChange={setSection} />

      {section === "market" && (
        <MarketMonitorPanel data={{
          price: c.price,
          change_pct: c.momentum_5d,
          volume_ratio: c.volume_ratio,
          rsi: c.rsi,
          momentum_5d: c.momentum_5d,
          stop_loss: c.stop_loss,
          target_price: c.target_price,
          near_breakout: c.near_breakout,
          timeframe: c.timeframe,
        }} />
      )}
      {section === "ai" && (
        <InlineAIPanel
          symbol={c.symbol}
          backendOnline={backendOnline}
          state={aiState}
          onLoad={loadAI}
        />
      )}
      {section === "sentiment" && (
        <InlineNewsPanel symbol={c.symbol} backendOnline={backendOnline} />
      )}
    </div>
  );
}

// ── Watchlist stock card ──────────────────────────────────────────────────────

function WatchCard({
  quote,
  onRemove,
  backendOnline,
}: {
  quote: Quote;
  onRemove: (symbol: string) => void;
  backendOnline: boolean;
}) {
  const [section, setSection] = useState<SectionKey | null>(null);
  const change = quote.change_pct ?? 0;

  // Lift AI state here so it survives tab switches (doesn't reset when section changes)
  const [aiState, setAiState] = useState<AIState>({
    analysis: null, loading: false, loaded: false, error: null,
  });

  async function loadAI() {
    if (!backendOnline || aiState.loading) return;
    setAiState(s => ({ ...s, loading: true, error: null }));
    try {
      const result = await api.analyze(quote.symbol);
      setAiState({ analysis: result, loading: false, loaded: true, error: null });
    } catch (e) {
      const msg = e instanceof Error ? e.message : "分析请求失败";
      setAiState(s => ({ ...s, loading: false, error: msg }));
    }
  }

  // Show AI signal badge on card header once we have a result
  const aiSignal = aiState.analysis?.signal ?? quote.analysis?.signal;

  return (
    <div className="rs-card">
      <div className="rs-card-header">
        <div className="rs-card-left">
          <span className="symbol" style={{ fontSize: 15 }}>{quote.symbol}</span>
          {aiSignal && (
            <span className="signal-badge" style={{ background: SIGNAL_BG[aiSignal] ?? "#64748b" }}>
              {aiSignal}
            </span>
          )}
          {quote.news_sentiment && !aiSignal && (
            <span className="signal-badge" style={{ background: SENTIMENT_COLOR[quote.news_sentiment] ?? "#64748b" }}>
              {quote.news_sentiment}
            </span>
          )}
        </div>
        <div className="rs-card-right">
          {quote.price > 0 && (
            <span className="rs-price">${quote.price.toFixed(2)}</span>
          )}
          {quote.price > 0 && (
            <span className={`rs-change ${change >= 0 ? "up" : "down"}`}>
              {change >= 0 ? "▲" : "▼"} {Math.abs(change).toFixed(2)}%
            </span>
          )}
          <button className="rs-remove-btn" onClick={() => onRemove(quote.symbol)} title="从 Watchlist 移除">✕</button>
        </div>
      </div>

      <SectionTabs active={section} onChange={setSection} />

      {section === "market" && (
        <MarketMonitorPanel data={{
          price: quote.price,
          change_pct: quote.change_pct,
          volume: quote.volume,
        }} />
      )}
      {section === "ai" && (
        <InlineAIPanel
          symbol={quote.symbol}
          backendOnline={backendOnline}
          state={aiState}
          onLoad={loadAI}
        />
      )}
      {section === "sentiment" && (
        <InlineNewsPanel symbol={quote.symbol} backendOnline={backendOnline} />
      )}
    </div>
  );
}

// ── Main component ────────────────────────────────────────────────────────────

export function StockResearch({ backendOnline }: Props) {
  // Run once before any state initializes
  clearStaleDefaults();

  const [scan, setScan] = useState<ScanResult | null>(null);
  const [scanning, setScanning] = useState(false);

  const [watchlist, setWatchlist] = useState<string[]>(loadWatchlist);
  // Initialize immediately with placeholders so cards render before API returns
  const [quotes, setQuotes] = useState<Quote[]>(() =>
    loadWatchlist().map(symbol => ({
      symbol, price: 0, prev_close: 0, change_pct: 0, volume: 0, timestamp: "",
    }))
  );
  const [newSymbol, setNewSymbol] = useState("");
  const [quotesLoading, setQuotesLoading] = useState(false);

  // Always-current ref so loadQuotes never captures a stale watchlist
  const watchlistRef = useRef(watchlist);
  useEffect(() => { watchlistRef.current = watchlist; }, [watchlist]);

  // Fetch scan
  const loadScan = useCallback(async () => {
    if (!backendOnline) return;
    try { setScan(await api.getScan()); } catch { /* offline */ }
  }, [backendOnline]);

  // Fetch watchlist quotes — each symbol individually so newly-added symbols
  // are always fetched regardless of backend watchlist.json state.
  const loadQuotes = useCallback(async () => {
    if (!backendOnline) return;
    const symbols = watchlistRef.current;
    if (symbols.length === 0) return;
    setQuotesLoading(true);
    try {
      const results = await Promise.allSettled(
        symbols.map(sym => api.getQuoteSingle(sym))
      );
      setQuotes(symbols.map((sym, i) => {
        const r = results[i];
        if (r.status === "fulfilled" && !("error" in r.value) && r.value.price > 0) {
          return r.value;
        }
        // Keep placeholder — price will stay 0 until next poll if still failing
        return { symbol: sym, price: 0, prev_close: 0, change_pct: 0, volume: 0, timestamp: "" };
      }));
    } catch { /* offline */ }
    finally { setQuotesLoading(false); }
  }, [backendOnline]);

  useEffect(() => {
    loadScan();
    loadQuotes();
    const id = setInterval(() => { loadScan(); loadQuotes(); }, 30_000);
    return () => clearInterval(id);
  }, [loadScan, loadQuotes]);

  // Sync backend watchlist to match local state on mount
  useEffect(() => {
    if (!backendOnline) return;
    // Push current watchlist to backend by fetching quotes (backend reconciles)
    // Backend watchlist.json was cleared; re-add any local symbols
    watchlist.forEach(sym => api.addToWatchlist(sym).catch(() => {}));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [backendOnline]); // only on mount / backend reconnect

  async function runScan() {
    setScanning(true);
    try {
      await api.triggerScan();
      const poll = setInterval(async () => {
        const result = await api.getScan();
        setScan(result);
        if (result.status !== "running") { clearInterval(poll); setScanning(false); }
      }, 3000);
      setTimeout(() => { clearInterval(poll); setScanning(false); }, 120_000);
    } catch { setScanning(false); }
  }

  function handleAdd() {
    const s = newSymbol.trim().toUpperCase();
    if (!s || watchlist.includes(s)) return;
    const next = [...watchlist, s];
    setWatchlist(next);
    saveWatchlist(next);
    watchlistRef.current = next;   // update ref immediately so loadQuotes sees the new symbol
    setQuotes(prev => [
      ...prev,
      { symbol: s, price: 0, prev_close: 0, change_pct: 0, volume: 0, timestamp: "" },
    ]);
    setNewSymbol("");
    api.addToWatchlist(s).catch(() => {});
    loadQuotes();   // fetch prices for the full updated list right away
  }

  function handleRemove(symbol: string) {
    const next = watchlist.filter(s => s !== symbol);
    setWatchlist(next);
    saveWatchlist(next);
    setQuotes(prev => prev.filter(q => q.symbol !== symbol));
    api.removeFromWatchlist(symbol).catch(() => {});
  }

  const top10 = (scan?.candidates ?? []).slice(0, 10);

  return (
    <div className="rs-container">
      {/* ══════════ Zone 1: S&P 500 Scan ══════════ */}
      <section className="rs-zone">
        <div className="rs-zone-header">
          <div>
            <h2 className="rs-zone-title">🔍 S&P 500 扫描</h2>
            <span className="rs-zone-meta">
              {scan?.status === "done" && scan.total_screened
                ? `扫描 ${scan.total_screened} 只股票 · Top ${top10.length} 候选`
                : scan?.status === "running"
                ? "扫描中…"
                : "点击运行扫描获取推荐"}
              {scan?.scanned_at && (
                <span style={{ marginLeft: 8, color: "var(--muted)" }}>
                  {new Date(scan.scanned_at + "Z").toLocaleTimeString()}
                </span>
              )}
            </span>
          </div>
          <button
            className="brief-generate-btn"
            onClick={runScan}
            disabled={scanning || !backendOnline}
          >
            {scanning ? "扫描中…" : "▶ 运行扫描"}
          </button>
        </div>

        {scan?.status === "error" && (
          <div className="brief-empty" style={{ padding: "16px 0" }}>
            <p className="brief-empty-text" style={{ color: "#ef4444" }}>{scan.error ?? "扫描出错"}</p>
          </div>
        )}

        {top10.length === 0 && scan?.status !== "running" && (
          <div className="rs-empty">
            <p>暂无扫描结果。点击「运行扫描」开始 S&P 500 分析。</p>
          </div>
        )}

        {scanning && top10.length === 0 && (
          <div className="rs-empty"><p>扫描中，请稍候…</p></div>
        )}

        <div className="rs-cards-list">
          {top10.map((c, i) => (
            <ScanCard
              key={c.symbol}
              rank={i + 1}
              candidate={c}
              backendOnline={backendOnline}
            />
          ))}
        </div>
      </section>

      {/* ══════════ Zone 2: Watchlist ══════════ */}
      <section className="rs-zone">
        <div className="rs-zone-header">
          <div>
            <h2 className="rs-zone-title">📋 自选股 Watchlist</h2>
            <span className="rs-zone-meta">{watchlist.length} 只股票</span>
          </div>
          <div className="rs-add-row">
            <input
              className="symbol-input"
              placeholder="添加股票（如 AMZN）"
              value={newSymbol}
              onChange={e => setNewSymbol(e.target.value.toUpperCase())}
              onKeyDown={e => e.key === "Enter" && handleAdd()}
              disabled={!backendOnline}
            />
            <button className="add-btn" onClick={handleAdd} disabled={!backendOnline}>
              添加
            </button>
          </div>
        </div>

        {quotesLoading && quotes.length === 0 && (
          <div className="rs-empty"><p>加载中…</p></div>
        )}

        {!quotesLoading && quotes.length === 0 && (
          <div className="rs-empty"><p>Watchlist 为空，请添加股票。</p></div>
        )}

        <div className="rs-cards-list">
          {quotes.map(q => (
            <WatchCard
              key={q.symbol}
              quote={q}
              onRemove={handleRemove}
              backendOnline={backendOnline}
            />
          ))}
        </div>
      </section>
    </div>
  );
}
