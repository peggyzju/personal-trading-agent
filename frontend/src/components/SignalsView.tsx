import { useState, useEffect, useCallback, useMemo, useRef } from "react";
import { api } from "../api/client";
import type { ScanCandidate, ScanResult, BudgetAllocation } from "../api/client";
import { TradeModal } from "./TradeModal";

interface Props { backendOnline: boolean }

type SigTab = "all" | "watchlist";

const SIGNAL_BG: Record<string, string> = {
  STRONG_BUY: "#16a34a",
  BUY:        "#22c55e",
  HOLD:       "#475569",
  SELL:       "#ef4444",
  WATCH:      "#d97706",
};

function aiBarColor(score: number): string {
  if (score >= 8) return "linear-gradient(90deg,#16a34a,#22c55e)";
  if (score >= 6) return "linear-gradient(90deg,#2563eb,#3b82f6)";
  if (score >= 4) return "linear-gradient(90deg,#b45309,#f59e0b)";
  return "linear-gradient(90deg,#991b1b,#ef4444)";
}

function fmtMktCap(mc: number): string {
  if (mc >= 1e12) return `$${(mc / 1e12).toFixed(1)}T`;
  if (mc >= 1e9)  return `$${(mc / 1e9).toFixed(1)}B`;
  return `$${(mc / 1e6).toFixed(0)}M`;
}

// Candidate with source tags attached
interface TaggedCandidate extends ScanCandidate {
  sourceTags: string[];
}

export function SignalsView({ backendOnline }: Props) {
  const [tab, setTab]             = useState<SigTab>("all");
  const [sp500Data, setSp500Data] = useState<ScanResult | null>(null);
  const [nasdaqData, setNasdaqData] = useState<ScanResult | null>(null);
  const [budget, setBudget]       = useState<BudgetAllocation | null>(null);
  const [watchlist, setWatchlist] = useState<string[]>([]);
  const [watchlistAnalysis, setWatchlistAnalysis] = useState<Record<string, import("../api/client").Analysis | null>>({});
  const [watchlistLoading, setWatchlistLoading]   = useState<Record<string, boolean>>({});
  const [addInput, setAddInput]   = useState("");
  const [addLoading, setAddLoading] = useState(false);
  const [scanning, setScanning]   = useState(false);

  // Load cache from localStorage on mount
  useEffect(() => {
    try {
      const c500 = localStorage.getItem("scan_cache_sp500");
      const cNQ  = localStorage.getItem("scan_cache_nasdaq");
      if (c500) setSp500Data(JSON.parse(c500));
      if (cNQ)  setNasdaqData(JSON.parse(cNQ));
    } catch { /* ignore */ }
  }, []);

  const load = useCallback(async () => {
    if (!backendOnline) return;
    const [sp500, nasdaq, bud] = await Promise.allSettled([
      api.getScan(), api.getScanNasdaq(), api.getBudget(),
    ]);
    if (sp500.status === "fulfilled") {
      const v = sp500.value;
      if ((v.candidates?.length ?? 0) > 0) {
        setSp500Data(v);
        try { localStorage.setItem("scan_cache_sp500", JSON.stringify(v)); } catch { /* ignore */ }
      } else {
        setSp500Data(prev => prev ? { ...prev, status: v.status } : v);
      }
    }
    if (nasdaq.status === "fulfilled") {
      const v = nasdaq.value;
      if ((v.candidates?.length ?? 0) > 0) {
        setNasdaqData(v);
        try { localStorage.setItem("scan_cache_nasdaq", JSON.stringify(v)); } catch { /* ignore */ }
      } else {
        setNasdaqData(prev => prev ? { ...prev, status: v.status } : v);
      }
    }
    if (bud.status === "fulfilled") setBudget(bud.value);
  }, [backendOnline]);

  const loadWatchlist = useCallback(async () => {
    if (!backendOnline) return;
    try { const d = await api.getWatchlist(); setWatchlist(d.symbols ?? []); }
    catch { /* ignore */ }
  }, [backendOnline]);

  useEffect(() => {
    load(); loadWatchlist();
    const id = setInterval(() => { load(); loadWatchlist(); }, 30_000);
    return () => clearInterval(id);
  }, [load, loadWatchlist]);

  // Auto-enrich cached candidates that lack fundamentals (fires once when data lands)
  const enrichingRef = useRef(false);
  useEffect(() => {
    if (!backendOnline) return;
    const candidates = sp500Data?.candidates ?? [];
    if (candidates.length === 0 || candidates.some(c => c.company_name)) return;
    if (enrichingRef.current) return;
    enrichingRef.current = true;
    api.enrichScan().then(result => {
      if ((result.candidates?.length ?? 0) > 0) {
        setSp500Data(result);
        try { localStorage.setItem("scan_cache_sp500", JSON.stringify(result)); } catch { /* ignore */ }
      }
    }).catch(() => { /* ignore */ }).finally(() => { enrichingRef.current = false; });
  }, [backendOnline, sp500Data]);

  async function handleScan() {
    setScanning(true);
    try {
      await api.triggerScan();
      let attempts = 0;
      const poll = setInterval(async () => {
        attempts++;
        const result = await api.getScan();
        if (result.status === "done" || result.status === "error" || attempts > 30) {
          clearInterval(poll);
          setScanning(false);
          if ((result.candidates?.length ?? 0) > 0) {
            setSp500Data(result);
            try { localStorage.setItem("scan_cache_sp500", JSON.stringify(result)); } catch { /* ignore */ }
          }
          const nq = await api.getScanNasdaq();
          if ((nq.candidates?.length ?? 0) > 0) {
            setNasdaqData(nq);
            try { localStorage.setItem("scan_cache_nasdaq", JSON.stringify(nq)); } catch { /* ignore */ }
          }
        }
      }, 3000);
    } catch { setScanning(false); }
  }

  async function handleAddToWatchlist(sym?: string) {
    const s = (sym ?? addInput).trim().toUpperCase();
    if (!s) return;
    setAddLoading(true);
    try {
      const result = await api.addToWatchlist(s);
      setWatchlist(result.symbols ?? []);
      if (!sym) setAddInput("");
    } catch { /* ignore */ }
    finally { setAddLoading(false); }
  }

  async function handleRemoveFromWatchlist(sym: string) {
    setWatchlist(prev => prev.filter(s => s !== sym)); // optimistic
    try { const r = await api.removeFromWatchlist(sym); setWatchlist(r.symbols ?? []); }
    catch { setWatchlist(prev => [...prev, sym]); } // revert on error
  }

  async function analyzeWatchlistStock(sym: string) {
    if (watchlistLoading[sym]) return;
    setWatchlistLoading(prev => ({ ...prev, [sym]: true }));
    try {
      const r = await api.analyze(sym);
      setWatchlistAnalysis(prev => ({ ...prev, [sym]: r }));
    } catch { /* ignore */ }
    finally { setWatchlistLoading(prev => ({ ...prev, [sym]: false })); }
  }

  // Merge + dedupe by symbol (keep higher ai_score), sort by ai_score desc
  const allCandidates: TaggedCandidate[] = useMemo(() => {
    const sp = (sp500Data?.candidates ?? []).filter(c => c.universe === "sp500" || !c.universe);
    const nq = nasdaqData?.candidates ?? [];
    const map = new Map<string, TaggedCandidate>();
    for (const c of sp) {
      map.set(c.symbol, { ...c, sourceTags: ["S&P"] });
    }
    for (const c of nq) {
      const ex = map.get(c.symbol);
      if (ex) {
        ex.sourceTags.push("NQ");
        if (c.ai_score > ex.ai_score) {
          map.set(c.symbol, { ...c, sourceTags: ex.sourceTags });
        }
      } else {
        map.set(c.symbol, { ...c, sourceTags: ["NQ"] });
      }
    }
    return Array.from(map.values()).sort((a, b) => b.ai_score - a.ai_score);
  }, [sp500Data, nasdaqData]);

  const isRunning   = sp500Data?.status === "running" || nasdaqData?.status === "running";
  const scanTime    = sp500Data?.scanned_at ?? nasdaqData?.scanned_at;
  const totalScreened = sp500Data?.total_screened;

  if (!backendOnline) {
    return <div className="brief-offline">Start the backend to view Signals.</div>;
  }

  return (
    <div className="signals-view">
      {/* ── Tab bar + scan button ── */}
      <div className="signals-header">
        <div className="signals-source-tabs">
          <button
            className={`signals-source-tab${tab === "all" ? " active" : ""}`}
            onClick={() => setTab("all")}
          >
            全部信号
            <span className="signals-count-badge">{allCandidates.length}</span>
          </button>
          <button
            className={`signals-source-tab${tab === "watchlist" ? " active" : ""}`}
            onClick={() => setTab("watchlist")}
          >
            我的自选
            <span className="signals-count-badge">{watchlist.length}</span>
          </button>
        </div>

        {tab === "all" && (
          <div className="signals-scan-wrap">
            {scanTime && (
              <span style={{ color: "var(--muted)", fontSize: 11 }}>
                {new Date(scanTime).toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" })} 更新
              </span>
            )}
            {totalScreened && (
              <span style={{ color: "var(--muted)", fontSize: 11 }}>共筛 {totalScreened} 只</span>
            )}
            <button
              className={`signals-scan-btn${scanning ? " scanning" : ""}`}
              onClick={handleScan}
              disabled={scanning || isRunning}
            >
              {scanning || isRunning ? "⏳ 扫描中…" : "🔍 立即扫描"}
            </button>
          </div>
        )}
      </div>

      {/* ── All signals tab ── */}
      {tab === "all" && (
        <AllSignalsView
          candidates={allCandidates}
          isRunning={isRunning}
          scanTime={scanTime}
          budget={budget}
          backendOnline={backendOnline}
          watchlist={watchlist}
          onAddToWatchlist={handleAddToWatchlist}
        />
      )}

      {/* ── Watchlist tab ── */}
      {tab === "watchlist" && (
        <WatchlistView
          watchlist={watchlist}
          analysis={watchlistAnalysis}
          loading={watchlistLoading}
          budget={budget}
          backendOnline={backendOnline}
          onAdd={handleAddToWatchlist}
          onRemove={handleRemoveFromWatchlist}
          onAnalyze={analyzeWatchlistStock}
          addInput={addInput}
          setAddInput={setAddInput}
          addLoading={addLoading}
        />
      )}
    </div>
  );
}

// ── All signals list ──────────────────────────────────────────────────────────

function AllSignalsView({
  candidates, isRunning, scanTime, budget, backendOnline, watchlist, onAddToWatchlist,
}: {
  candidates: TaggedCandidate[];
  isRunning: boolean;
  scanTime?: string;
  budget: BudgetAllocation | null;
  backendOnline: boolean;
  watchlist: string[];
  onAddToWatchlist: (sym: string) => void;
}) {
  if (candidates.length === 0 && !isRunning) {
    return (
      <div className="brief-empty">
        <p className="brief-empty-text">点击「立即扫描」获取最新信号</p>
      </div>
    );
  }
  if (candidates.length === 0 && isRunning) {
    return (
      <div className="brief-empty">
        <p className="brief-empty-text">⏳ 扫描运行中，请稍候…</p>
      </div>
    );
  }

  return (
    <div className="sc-list">
      {isRunning && (
        <div className="signals-stale-banner" style={{ background: "rgba(59,130,246,.1)", borderColor: "rgba(59,130,246,.3)", color: "#93c5fd" }}>
          ⏳ 新扫描进行中，以下为上次结果
        </div>
      )}
      {!isRunning && scanTime && (
        <div className="signals-stale-banner">
          📦 缓存结果 · {new Date(scanTime).toLocaleString("zh-CN", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" })} · 点击「立即扫描」刷新
        </div>
      )}
      {candidates.map((c, i) => (
        <SignalCard
          key={c.symbol}
          rank={i + 1}
          candidate={c}
          budget={budget}
          backendOnline={backendOnline}
          inWatchlist={watchlist.includes(c.symbol)}
          onAddToWatchlist={() => onAddToWatchlist(c.symbol)}
        />
      ))}
    </div>
  );
}

// ── Big signal card ───────────────────────────────────────────────────────────

type CardSection = "ai" | "sentiment" | null;

function SignalCard({
  rank, candidate: c, budget, backendOnline, inWatchlist, onAddToWatchlist,
}: {
  rank: number;
  candidate: TaggedCandidate;
  budget: BudgetAllocation | null;
  backendOnline: boolean;
  inWatchlist: boolean;
  onAddToWatchlist: () => void;
}) {
  const [showModal, setShowModal] = useState(false);
  const [section, setSection]     = useState<CardSection>(null);
  const [aiResult, setAiResult]   = useState<import("../api/client").Analysis | null>(null);
  const [aiLoading, setAiLoading] = useState(false);
  const [sentiment, setSentiment] = useState<import("../api/client").NewsSentiment | null>(null);
  const [sentLoading, setSentLoading] = useState(false);
  const [addedToWl, setAddedToWl] = useState(inWatchlist);

  const isBuyable = (c.signal === "STRONG_BUY" || c.signal === "BUY") && !c.owned;
  const portfolioValue = budget?.portfolio_value ?? 100_000;
  const stop = c.stop_loss ?? (c.price ? c.price * 0.97 : undefined);
  const suggestedNotional = isBuyable && stop && c.price && stop < c.price
    ? Math.min(portfolioValue * 0.02 / (c.price - stop) * c.price, portfolioValue * 0.10)
    : null;

  const stopPct   = stop && c.price ? ((stop - c.price) / c.price * 100) : null;
  const targetPct = c.target_price && c.price ? ((c.target_price - c.price) / c.price * 100) : null;

  function toggleSection(key: CardSection) {
    const next = section === key ? null : key;
    setSection(next);
    if (next === "ai" && !aiResult && !aiLoading) {
      setAiLoading(true);
      api.analyze(c.symbol).then(r => { setAiResult(r); setAiLoading(false); }).catch(() => setAiLoading(false));
    }
    if (next === "sentiment" && !sentiment && !sentLoading) {
      setSentLoading(true);
      api.analyzeNewsSentiment(c.symbol).then(r => { setSentiment(r); setSentLoading(false); }).catch(() => setSentLoading(false));
    }
  }

  function handleAddWl() {
    if (addedToWl) return;
    setAddedToWl(true);
    onAddToWatchlist();
  }

  const isSell = c.signal === "SELL";

  return (
    <div className={`sc-card${isBuyable ? " sc-card-buyable" : isSell ? " sc-card-sell" : ""}`}>
      {showModal && (
        <TradeModal
          symbol={c.symbol}
          side="buy"
          suggestedPrice={c.price}
          stopLoss={c.stop_loss}
          targetPrice={c.target_price}
          onClose={() => setShowModal(false)}
          onSuccess={() => setShowModal(false)}
        />
      )}

      {/* ── Row 1: rank · symbol · signal · tags · AI score ── */}
      <div className="sc-header">
        <div className="sc-header-left">
          <span className="sc-rank">#{rank}</span>
          <strong className="sc-symbol">{c.symbol}</strong>
          <span className="sc-signal-badge" style={{ background: SIGNAL_BG[c.signal] ?? "#475569" }}>
            {c.signal?.replace("_", " ")}
          </span>
          {c.sourceTags.map(t => (
            <span key={t} className={`sc-source-tag sc-src-${t === "S&P" ? "sp" : "nq"}`}>{t}</span>
          ))}
          {c.owned && <span className="sc-owned-badge">持仓中</span>}
        </div>
        <div className="sc-ai-score">
          <div className="sc-ai-bar-wrap">
            <div className="sc-ai-bar-fill" style={{ width: `${(c.ai_score / 10) * 100}%`, background: aiBarColor(c.ai_score) }} />
          </div>
          <span className="sc-ai-num">{c.ai_score.toFixed(1)}<span className="sc-ai-denom">/10</span></span>
        </div>
      </div>

      {/* ── Row 2: company name + sector ── */}
      {(c.company_name || c.sector) && (
        <div className="sc-company-row">
          {c.company_name && <span className="sc-company-name">{c.company_name}</span>}
          {c.sector && <span className="sc-company-sector">· {c.sector}</span>}
        </div>
      )}

      {/* ── Row 3: price · change · fundamentals · technicals ── */}
      <div className="sc-data-row">
        <span className="sc-price">${c.price?.toFixed(2)}</span>
        {c.momentum_5d != null && (
          <span className="sc-change" style={{ color: c.momentum_5d >= 0 ? "#22c55e" : "#ef4444" }}>
            {c.momentum_5d >= 0 ? "+" : ""}{c.momentum_5d.toFixed(1)}%
          </span>
        )}
        {(c.pe_ratio || c.market_cap || c.beta) && <span className="sc-data-sep">|</span>}
        {c.pe_ratio && <span className="sc-fund-chip">P/E {c.pe_ratio.toFixed(0)}x</span>}
        {c.market_cap && <span className="sc-fund-chip">{fmtMktCap(c.market_cap)}</span>}
        {c.beta != null && <span className="sc-fund-chip">β {c.beta.toFixed(1)}</span>}
        {(c.rsi != null || c.volume_ratio != null || c.near_breakout) && <span className="sc-data-sep">|</span>}
        {c.rsi != null && (
          <span className="sc-tech-chip" style={{ color: c.rsi > 70 ? "#ef4444" : c.rsi < 30 ? "#22c55e" : "#f59e0b" }}>
            RSI {c.rsi.toFixed(0)}
          </span>
        )}
        {c.volume_ratio != null && (
          <span className="sc-tech-chip" style={{ color: c.volume_ratio >= 1.5 ? "#22c55e" : "#475569" }}>
            量{c.volume_ratio.toFixed(1)}x
          </span>
        )}
        {c.near_breakout && <span className="sc-tech-chip" style={{ color: "#22c55e" }}>⚡突破</span>}
        {suggestedNotional && <span className="sc-data-sep">|</span>}
        {suggestedNotional && <span className="sc-suggested">推荐 ${suggestedNotional.toFixed(0)}</span>}
      </div>

      {/* ── Row 4: stop / target visual ── */}
      {(stopPct != null || targetPct != null) && (
        <div className="sc-rr-row">
          {stopPct != null && (
            <span className="sc-rr-stop">
              止损 ${stop?.toFixed(2)} <span style={{ color: "#ef4444" }}>({stopPct.toFixed(1)}%)</span>
            </span>
          )}
          <div className="sc-rr-bar">
            {stopPct != null && <div className="sc-rr-loss" style={{ width: `${Math.min(50, Math.abs(stopPct) * 5)}%` }} />}
            <div className="sc-rr-mid" />
            {targetPct != null && <div className="sc-rr-gain" style={{ width: `${Math.min(50, targetPct * 3)}%` }} />}
          </div>
          {targetPct != null && (
            <span className="sc-rr-target">
              目标 ${c.target_price?.toFixed(2)} <span style={{ color: "#22c55e" }}>(+{targetPct.toFixed(1)}%)</span>
            </span>
          )}
        </div>
      )}

      {/* ── Row 5: AI reason (starts with company description) ── */}
      {c.reason && <p className="sc-reason">{c.reason}</p>}

      {/* ── Row 6: actions ── */}
      <div className="sc-actions">
        {isBuyable ? (
          <button className="sc-action-buy" onClick={() => setShowModal(true)}>
            买入{suggestedNotional ? ` $${suggestedNotional.toFixed(0)}` : ""}
          </button>
        ) : c.owned ? (
          <span className="sc-action-owned">持仓中</span>
        ) : (
          <span className="sc-action-watch">观察</span>
        )}
        <button
          className={`sc-action-btn${section === "ai" ? " active" : ""}`}
          onClick={() => toggleSection("ai")}
          disabled={!backendOnline}
        >
          🏢 公司详情
        </button>
        <button
          className={`sc-action-btn${section === "sentiment" ? " active" : ""}`}
          onClick={() => toggleSection("sentiment")}
          disabled={!backendOnline}
        >
          📰 舆情
        </button>
        <button
          className={`sc-action-wl${addedToWl ? " added" : ""}`}
          onClick={handleAddWl}
          disabled={addedToWl}
          title={addedToWl ? "已在自选列表" : "加入自选"}
        >
          {addedToWl ? "✓ 已自选" : "+ 自选"}
        </button>
      </div>

      {/* ── Expand: AI analysis ── */}
      {section === "ai" && (
        <div className="sc-expand-panel">
          {aiLoading && <span style={{ color: "var(--muted)", fontSize: 12 }}>加载分析中…</span>}
          {aiResult && (
            <>
              <p style={{ color: "#cbd5e1", margin: "0 0 10px", lineHeight: 1.6, fontSize: 12 }}>
                {aiResult.reasoning}
              </p>
              <div style={{ display: "flex", gap: 12, flexWrap: "wrap", paddingTop: 8, borderTop: "1px solid var(--border)" }}>
                <span style={{ color: aiResult.signal === "BUY" ? "#22c55e" : aiResult.signal === "SELL" ? "#ef4444" : "#64748b", fontWeight: 700 }}>
                  {aiResult.signal}
                </span>
                <span style={{ color: "var(--muted)" }}>信心 {Math.round(aiResult.confidence * 100)}%</span>
                {aiResult.target_price && <span style={{ color: "#22c55e" }}>目标 ${aiResult.target_price.toFixed(2)}</span>}
                {aiResult.stop_loss && <span style={{ color: "#ef4444" }}>止损 ${aiResult.stop_loss.toFixed(2)}</span>}
              </div>
              {aiResult.key_risks?.length > 0 && (
                <div style={{ marginTop: 6 }}>
                  <span style={{ color: "var(--muted)", fontSize: 11 }}>风险：</span>
                  {aiResult.key_risks.map((r, i) => (
                    <span key={i} style={{ color: "#f59e0b", fontSize: 11, marginLeft: 4 }}>• {r}</span>
                  ))}
                </div>
              )}
            </>
          )}
        </div>
      )}

      {/* ── Expand: Sentiment ── */}
      {section === "sentiment" && (
        <div className="sc-expand-panel">
          {sentLoading && <span style={{ color: "var(--muted)", fontSize: 12 }}>加载舆情…</span>}
          {sentiment && (
            <>
              <div style={{ display: "flex", gap: 10, marginBottom: 8, flexWrap: "wrap" }}>
                <span style={{ color: sentiment.overall === "BULLISH" ? "#22c55e" : sentiment.overall === "BEARISH" ? "#ef4444" : "#f59e0b", fontWeight: 700 }}>
                  {sentiment.overall}
                </span>
                <span style={{ color: "#cbd5e1" }}>{sentiment.key_insight}</span>
              </div>
              {sentiment.watch_for && (
                <p style={{ color: "#f59e0b", margin: "0 0 6px", fontSize: 12 }}>⚠️ {sentiment.watch_for}</p>
              )}
              {sentiment.items?.slice(0, 3).map((item, i) => (
                <div key={i} style={{ borderTop: "1px solid #1e293b", paddingTop: 6, marginTop: 6 }}>
                  <a href={item.url} target="_blank" rel="noreferrer"
                    style={{ color: "#93c5fd", fontWeight: 600, textDecoration: "none", fontSize: 12 }}>
                    {item.title}
                  </a>
                  <p style={{ color: "var(--muted)", margin: "2px 0 0", fontSize: 11 }}>{item.summary}</p>
                </div>
              ))}
            </>
          )}
        </div>
      )}
    </div>
  );
}

// ── Watchlist view ────────────────────────────────────────────────────────────

function WatchlistView({
  watchlist, analysis, loading, budget, backendOnline,
  onAdd, onRemove, onAnalyze, addInput, setAddInput, addLoading,
}: {
  watchlist: string[];
  analysis: Record<string, import("../api/client").Analysis | null>;
  loading: Record<string, boolean>;
  budget: BudgetAllocation | null;
  backendOnline: boolean;
  onAdd: (sym?: string) => void;
  onRemove: (sym: string) => void;
  onAnalyze: (sym: string) => void;
  addInput: string;
  setAddInput: (v: string) => void;
  addLoading: boolean;
}) {
  return (
    <div className="watchlist-view">
      <div className="watchlist-add-bar">
        <input
          className="watchlist-input"
          placeholder="股票代码，例: AAPL"
          value={addInput}
          onChange={e => setAddInput(e.target.value.toUpperCase())}
          onKeyDown={e => e.key === "Enter" && onAdd()}
          maxLength={10}
        />
        <button
          className="watchlist-add-btn"
          onClick={() => onAdd()}
          disabled={addLoading || !addInput.trim()}
        >
          {addLoading ? "…" : "+ 添加"}
        </button>
      </div>

      {watchlist.length === 0 ? (
        <div className="brief-empty">
          <p className="brief-empty-text">自选列表为空，在信号页点击「+ 自选」或在此输入代码添加</p>
        </div>
      ) : (
        <div className="watchlist-cards">
          {watchlist.map(sym => {
            const ai = analysis[sym];
            const isLoading = loading[sym] ?? false;
            return (
              <div key={sym} className="watchlist-card">
                <div className="watchlist-card-header">
                  <div className="watchlist-card-left">
                    <strong className="signal-symbol">{sym}</strong>
                    {ai && (
                      <>
                        <span className="signal-badge" style={{ background: SIGNAL_BG[ai.signal] ?? "#64748b" }}>
                          {ai.signal}
                        </span>
                        <span style={{ color: "#f59e0b", fontSize: 11 }}>
                          信心 {Math.round(ai.confidence * 100)}%
                        </span>
                      </>
                    )}
                    {!ai && !isLoading && <span style={{ color: "var(--muted)", fontSize: 11 }}>未分析</span>}
                    {isLoading && <span style={{ color: "var(--muted)", fontSize: 11 }}>分析中…</span>}
                  </div>
                  <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
                    {ai?.price && <span className="signal-price">${ai.price.toFixed(2)}</span>}
                    {!ai && !isLoading && (
                      <button className="watchlist-analyze-btn" onClick={() => onAnalyze(sym)} disabled={!backendOnline}>
                        立即分析
                      </button>
                    )}
                    {ai && (
                      <button className="watchlist-analyze-btn"
                        onClick={() => onAnalyze(sym)} disabled={!backendOnline || isLoading}
                        style={{ background: "#1e293b", color: "var(--muted)" }}>
                        {isLoading ? "…" : "刷新"}
                      </button>
                    )}
                    <button className="watchlist-remove-btn" onClick={() => onRemove(sym)} title="移除">✕</button>
                  </div>
                </div>
                {ai && (
                  <div className="watchlist-ai-detail">
                    <div className="signal-tech-strip" style={{ marginBottom: 6 }}>
                      {ai.target_price && (
                        <span className="signal-tech-item" style={{ color: "#22c55e" }}>目标 ${ai.target_price.toFixed(2)}</span>
                      )}
                      {ai.stop_loss && (
                        <span className="signal-tech-item" style={{ color: "#ef4444" }}>止损 ${ai.stop_loss.toFixed(2)}</span>
                      )}
                    </div>
                    <p style={{ color: "#cbd5e1", fontSize: 12, margin: "0 0 4px", lineHeight: 1.5 }}>{ai.reasoning}</p>
                    {ai.key_risks?.length > 0 && (
                      <div>{ai.key_risks.map((r, i) => (
                        <span key={i} style={{ color: "#f59e0b", fontSize: 11, marginRight: 8 }}>• {r}</span>
                      ))}</div>
                    )}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
