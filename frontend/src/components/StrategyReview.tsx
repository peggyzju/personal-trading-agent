import { useState, useEffect, useCallback } from "react";
import { api } from "../api/client";
import type { StrategyReview, StrategyIterationOp, DebateResult, StockDebateResult, StrategyNote, OverrideHistoryEntry, ParamChange, PostmortemResult, PostmortemTrade } from "../api/client";

interface Props { backendOnline: boolean }

const PRIORITY_COLOR: Record<string, string> = {
  HIGH: "#ef4444",
  MEDIUM: "#f59e0b",
  LOW: "#64748b",
};

export function StrategyReviewPanel({ backendOnline }: Props) {
  const [subTab, setSubTab] = useState<"daily" | "postmortem">("daily");

  if (!backendOnline) {
    return <div className="brief-offline">启动后端服务以查看策略复盘。</div>;
  }

  return (
    <div className="sr-container">
      {/* Sub-tab switcher */}
      <div className="sr-subtab-bar">
        <button
          className={`sr-subtab-btn${subTab === "daily" ? " active" : ""}`}
          onClick={() => setSubTab("daily")}
        >
          📈 每日复盘
        </button>
        <button
          className={`sr-subtab-btn${subTab === "postmortem" ? " active" : ""}`}
          onClick={() => setSubTab("postmortem")}
        >
          🔍 周度复盘
        </button>
      </div>

      {subTab === "daily" && <DailyReviewPanel backendOnline={backendOnline} />}
      {subTab === "postmortem" && <PostMortemPanel backendOnline={backendOnline} />}
    </div>
  );
}


function DailyReviewPanel({ backendOnline }: Props) {
  const [review, setReview] = useState<StrategyReview | null>(null);
  const [generating, setGenerating] = useState(false);
  const [status, setStatus] = useState<string>("");

  const load = useCallback(async () => {
    if (!backendOnline) return;
    try {
      const r = await api.getStrategyReview();
      if (r && "date" in r) setReview(r as StrategyReview);
      else if ((r as { status?: string })?.status === "running") setStatus("running");
    } catch { /* no review yet */ }
  }, [backendOnline]);

  useEffect(() => {
    load();
    const id = setInterval(load, 30_000);
    return () => clearInterval(id);
  }, [load]);

  async function generate() {
    setGenerating(true);
    setStatus("running");
    try {
      await api.generateStrategyReview();
      let tries = 0;
      const poll = setInterval(async () => {
        tries++;
        try {
          const r = await api.getStrategyReview();
          if (r && "date" in r) {
            setReview(r as StrategyReview);
            setStatus("done");
            clearInterval(poll);
            setGenerating(false);
          }
        } catch { /* still running */ }
        if (tries > 30) { clearInterval(poll); setGenerating(false); }
      }, 3000);
    } catch {
      setGenerating(false);
      setStatus("");
    }
  }

  return (
    <>
      <div className="sr-header">
        <div>
          <h2 className="sr-title">📈 每日策略复盘</h2>
          <p className="sr-subtitle">收盘后自动生成 · 追踪 15%/月目标进度</p>
        </div>
        <button
          className="brief-generate-btn"
          onClick={generate}
          disabled={generating || !backendOnline}
        >
          {generating ? "生成中…" : "▶ 立即生成"}
        </button>
      </div>

      {!review && status !== "running" && (
        <div className="brief-empty" style={{ padding: "40px 0" }}>
          <p className="brief-empty-text">
            暂无复盘数据。收盘后（美东 4:15 PM）自动生成，或点击「立即生成」。
          </p>
        </div>
      )}
      {status === "running" && !review && (
        <div className="brief-empty" style={{ padding: "40px 0" }}>
          <p className="brief-empty-text">⏳ Claude 正在分析今日交易数据…（约 15-20 秒）</p>
        </div>
      )}
      {review && <ReviewCard review={review} />}
    </>
  );
}


// ── Post-Mortem Panel ──────────────────────────────────────────────────────────

const PM_PERIODS = [
  { label: "本周 7d",  days: 7 },
  { label: "双周 14d", days: 14 },
  { label: "本月 30d", days: 30 },
  { label: "本季 90d", days: 90 },
];

function PostMortemPanel({ backendOnline }: Props) {
  const [days, setDays]           = useState(7);
  const [loading, setLoading]     = useState(false);
  const [result, setResult]       = useState<PostmortemResult | null>(null);
  const [error, setError]         = useState<string | null>(null);

  async function generate() {
    if (!backendOnline) return;
    setLoading(true);
    setError(null);
    try {
      const r = await api.getPostmortem(days, 3);
      setResult(r);
      if (r.error) setError(r.error);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "请求失败");
    } finally {
      setLoading(false);
    }
  }

  return (
    <>
      <div className="sr-header">
        <div>
          <h2 className="sr-title">🔍 周度复盘</h2>
          <p className="sr-subtitle">AI 自我分析赢家/亏损特征，识别 Prompt 漏洞</p>
        </div>
      </div>

      {/* Period + generate */}
      <div className="pm-controls">
        <div className="pm-period-group">
          {PM_PERIODS.map(p => (
            <button
              key={p.days}
              className={`pm-period-btn${days === p.days ? " active" : ""}`}
              onClick={() => setDays(p.days)}
            >
              {p.label}
            </button>
          ))}
        </div>
        <button className="brief-generate-btn" onClick={generate} disabled={loading}>
          {loading ? "分析中…" : "▶ 生成复盘"}
        </button>
        {result && (
          <span className="pm-gen-time">
            生成于 {new Date(result.generated_at).toLocaleTimeString("zh-CN")}
          </span>
        )}
      </div>

      {/* Error */}
      {error && <div className="pm-error">⚠ {error}</div>}

      {/* Empty state */}
      {!result && !loading && (
        <div className="brief-empty" style={{ padding: "40px 0" }}>
          <p className="brief-empty-text">
            选择时间段，点击「生成复盘」。<br />
            Claude 会分析赢家/亏损特征，并给出 Prompt 改进建议。
          </p>
        </div>
      )}

      {loading && (
        <div className="brief-empty" style={{ padding: "40px 0" }}>
          <p className="brief-empty-text">⏳ 正在拉取持仓数据并分析…（约 20-30 秒）</p>
        </div>
      )}

      {result && !loading && (
        <>
          {/* Stats row */}
          <div className="pm-stats-row">
            {[
              { label: "时间段交易数", value: String(result.total) },
              { label: "有PnL数据", value: String(result.enriched) },
              { label: "胜率", value: result.stats.win_rate != null ? result.stats.win_rate + "%" : "—", cls: result.stats.win_rate >= 50 ? "pos" : "neg" },
              { label: "平均PnL", value: result.stats.avg_pnl != null ? fmtPct(result.stats.avg_pnl) : "—", cls: (result.stats.avg_pnl ?? 0) >= 0 ? "pos" : "neg" },
              { label: "最好/最差", value: `${fmtPct(result.stats.best_pnl)} / ${fmtPct(result.stats.worst_pnl)}`, mixed: true },
            ].map(s => (
              <div className="pm-stat-card" key={s.label}>
                <div className="pm-stat-label">{s.label}</div>
                <div className={`pm-stat-value${s.cls ? " " + s.cls : ""}`}>{s.value}</div>
              </div>
            ))}
          </div>

          {/* Winner / Loser tables */}
          {(result.winners.length > 0 || result.losers.length > 0) && (
            <div className="pm-trades-grid">
              <TradeGroup title="赢家组 🏆" trades={result.winners} isWinner />
              <TradeGroup title="亏损组 ⚠️" trades={result.losers} isWinner={false} />
            </div>
          )}

          {/* Claude analysis */}
          {result.analysis && (
            <div className="pm-analysis-card">
              <div className="pm-analysis-title">Claude 自我复盘报告</div>
              <div className="pm-analysis-body">
                {result.analysis.split("\n").map((line, i) => {
                  if (line.startsWith("## ")) return <h3 key={i} className="pm-analysis-h">{line.slice(3)}</h3>;
                  if (line.startsWith("### ")) return <h4 key={i} className="pm-analysis-sh">{line.slice(4)}</h4>;
                  if (line.startsWith("- ")) return <p key={i} className="pm-analysis-bullet">• {line.slice(2)}</p>;
                  if (line.trim() === "") return <div key={i} className="pm-analysis-spacer" />;
                  return <p key={i} className="pm-analysis-p">{line}</p>;
                })}
              </div>
            </div>
          )}
        </>
      )}
    </>
  );
}

function fmtPct(v: number | null | undefined): string {
  if (v == null) return "—";
  return (v >= 0 ? "+" : "") + v.toFixed(1) + "%";
}

function TradeGroup({ title, trades, isWinner }: { title: string; trades: PostmortemTrade[]; isWinner: boolean }) {
  return (
    <div className={`pm-trade-group${isWinner ? " pm-winners" : " pm-losers"}`}>
      <div className="pm-trade-group-title">{title}</div>
      {trades.length === 0 ? (
        <div className="pm-trade-empty">无数据</div>
      ) : (
        trades.map((t, i) => <TradeRow key={i} t={t} isWinner={isWinner} />)
      )}
    </div>
  );
}

function TradeRow({ t, isWinner }: { t: PostmortemTrade; isWinner: boolean }) {
  const [expanded, setExpanded] = useState(false);
  const pnlCls = (t.pnl_pct ?? 0) >= 0 ? "pos" : "neg";
  return (
    <div className="pm-trade-row">
      <div className="pm-trade-top">
        <span className="pm-trade-sym">{t.symbol}</span>
        {t.pnl_source === "live" && <span className="pm-live-tag">浮动</span>}
        <span className={`pm-trade-pnl ${pnlCls}`}>{fmtPct(t.pnl_pct)}</span>
      </div>
      <div className="pm-trade-meta">
        信号: {t.signal ?? "—"} | 置信度: {t.confidence ?? "—"} | RSI: {t.rsi ?? "—"} | 5d动量: {fmtPct(t.momentum_5d)}
      </div>
      <div className="pm-trade-meta">
        入场 ${t.fill_price?.toFixed(2) ?? "—"} → 现价 ${t.current_price ?? "—"} | 止损 ${t.stop_loss?.toFixed(2) ?? "—"} | 目标 ${t.target_price?.toFixed(2) ?? "—"}
      </div>
      {t.reason && (
        <>
          <button className="pm-reason-toggle" onClick={() => setExpanded(e => !e)}>
            {expanded ? "▲ 收起判断" : "▶ 当时判断"}
          </button>
          {expanded && <div className="pm-reason-text">{t.reason}</div>}
        </>
      )}
    </div>
  );
}


function ReviewCard({ review: r }: { review: StrategyReview }) {
  const adoptItems = r.iteration_opportunities.filter(op => op.verdict === "ADOPT");
  const otherItems = r.iteration_opportunities.filter(op => op.verdict !== "ADOPT");
  const [showOthers, setShowOthers] = useState(false);

  return (
    <div className="sr-card">
      {/* One-line summary */}
      <div className="sr-summary-bar">
        <span className="sr-date-label">
          {new Date(r.date + "T12:00:00").toLocaleDateString("en-US", { weekday: "short", month: "short", day: "numeric" })}
        </span>
        <span className="sr-one-line">{r.one_line_summary}</span>
      </div>

      {/* Iteration opportunities — ADOPT first */}
      <div className="sr-section">
        <h3 className="sr-section-title">🔁 迭代机会</h3>
        <p className="sr-progress-note">{r.monthly_progress_note}</p>
        <div className="sr-iter-list">
          {adoptItems.map((op, i) => (
            <IterCard key={i} op={op} reviewDate={r.date} />
          ))}
          {otherItems.length > 0 && (
            <>
              <button className="sr-others-toggle" onClick={() => setShowOthers(s => !s)}>
                {showOthers ? "▲ 收起" : `▶ 查看其余 ${otherItems.length} 条观察`}
              </button>
              {showOthers && otherItems.map((op, i) => (
                <IterCard key={`other-${i}`} op={op} reviewDate={r.date} />
              ))}
            </>
          )}
        </div>
      </div>

      {/* Tomorrow focus */}
      <div className="sr-section sr-tomorrow">
        <h3 className="sr-section-title">📅 明日关注</h3>
        <p className="sr-text">{r.tomorrow_focus}</p>
      </div>
    </div>
  );
}

type IterDecision = "adopt" | "hold" | "reject" | "done" | null;

const VERDICT_COLOR: Record<string, string> = {
  // iteration verdicts
  ADOPT: "#22c55e", HOLD: "#f59e0b", REJECT: "#ef4444",
  // stock debate verdicts
  STRONG_BUY: "#22c55e", BUY: "#86efac",
  SELL: "#f87171", STRONG_SELL: "#ef4444", AVOID: "#ef4444",
};
const VERDICT_LABEL: Record<string, string> = {
  ADOPT: "建议采纳", HOLD: "观察", REJECT: "不建议",
};

const ITER_STORE = "strategy_iter_decisions";

function iterKey(reviewDate: string, title: string) {
  return `${reviewDate}::${title}`;
}
function readDecisions(): Record<string, IterDecision> {
  try { return JSON.parse(localStorage.getItem(ITER_STORE) ?? "{}"); } catch { return {}; }
}
function saveDecision(reviewDate: string, title: string, d: IterDecision) {
  try {
    const all = readDecisions();
    if (d === null) delete all[iterKey(reviewDate, title)];
    else all[iterKey(reviewDate, title)] = d;
    localStorage.setItem(ITER_STORE, JSON.stringify(all));
  } catch { /* ignore */ }
}

function IterCard({ op, reviewDate }: { op: StrategyIterationOp; reviewDate: string }) {
  const [decision, setDecision]       = useState<IterDecision>(() => readDecisions()[iterKey(reviewDate, op.title)] ?? null);
  const [showBasis, setShowBasis]     = useState(false);
  const [extracting, setExtracting]   = useState(false);
  const [paramChanges, setParamChanges] = useState<ParamChange[] | null>(null);
  const [applying, setApplying]       = useState(false);
  const [toast, setToast]             = useState<string | null>(null);

  function setDec(d: IterDecision) {
    setDecision(d);
    saveDecision(reviewDate, op.title, d);
  }

  function showToast(msg: string) {
    setToast(msg);
    setTimeout(() => setToast(null), 8000);
  }

  async function handleAdopt() {
    if (decision === "adopt") { setDec(null); setParamChanges(null); return; }
    if (decision === "done")  { setDec("adopt"); return; }

    setExtracting(true);
    try {
      const result = await api.extractParams(op);
      if (result.mappable && result.params.length > 0) {
        setParamChanges(result.params);
        setDec("adopt");
      } else {
        // Qualitative — auto-add as strategy note, then trigger Rex immediately
        const noteText = op.synthesis ?? op.description ?? op.title;
        await api.addNote(noteText, reviewDate);
        setDec("done");
        try {
          await api.runAgent();
          showToast("📝 策略记忆已更新，Rex 已触发");
        } catch {
          showToast("📝 策略记忆已更新，Rex 下次运行时生效");
        }
      }
    } catch {
      setDec("adopt");
    } finally {
      setExtracting(false);
    }
  }

  async function applyParams() {
    if (!paramChanges) return;
    setApplying(true);
    try {
      const patch: Record<string, number | string> = { reason: op.title, source_review_date: reviewDate };
      for (const c of paramChanges) patch[c.name] = c.proposed;
      await api.saveOverrides(patch as Parameters<typeof api.saveOverrides>[0]);
      setParamChanges(null);
      setDec("done");
      // Immediately trigger Rex so new params take effect now
      try {
        await api.runAgent();
        showToast("✅ 参数已应用，Rex 已触发");
      } catch {
        showToast("✅ 参数已保存，Rex 下次运行时生效");
      }
    } catch {
      showToast("❌ 应用失败，请重试");
    } finally {
      setApplying(false);
    }
  }

  function cancelParams() { setParamChanges(null); setDec(null); }

  function decide(d: "hold" | "reject") {
    setDec(d === decision ? null : d);
    setParamChanges(null);
  }

  const hasDebate = !!(op.trading_view || op.backtest_view);
  const verdict   = op.verdict;
  const cardCls   = `sr-iter-card${decision === "done" ? " sr-iter-done" : decision === "adopt" ? " sr-iter-adopted" : decision === "reject" ? " sr-iter-rejected" : ""}`;

  return (
    <div className={cardCls}>
      {toast && <div className="sr-iter-toast">{toast}</div>}

      {/* Header */}
      <div className="sr-iter-header">
        <strong className="sr-iter-title">{op.title}</strong>
        <div style={{ display: "flex", gap: 6, alignItems: "center" }}>
          {verdict && (
            <span className="sr-verdict-badge" style={{ color: VERDICT_COLOR[verdict] ?? "#f59e0b" }}>
              {VERDICT_LABEL[verdict] ?? verdict}
            </span>
          )}
          <span className="sr-priority-badge" style={{ color: PRIORITY_COLOR[op.priority] ?? "#64748b" }}>
            {op.priority}
          </span>
        </div>
      </div>

      {op.synthesis ? (
        <p className="sr-iter-synthesis">{op.synthesis}</p>
      ) : op.description ? (
        <p className="sr-iter-desc">{op.description}</p>
      ) : null}

      <span className="sr-iter-impact">预期影响: {op.expected_impact}</span>

      {hasDebate && (
        <button className="sr-basis-toggle" onClick={() => setShowBasis(s => !s)}>
          {showBasis ? "▲ 收起辩论依据" : "▶ 查看辩论依据"}
        </button>
      )}
      {showBasis && hasDebate && (
        <div className="sr-debate-panel">
          {op.trading_view && (
            <div className="sr-debate-side sr-debate-pro">
              <div className="sr-debate-label">📈 交易 Agent · 执行视角</div>
              <p className="sr-debate-text">{op.trading_view}</p>
            </div>
          )}
          {op.backtest_view && (
            <div className="sr-debate-side sr-debate-con">
              <div className="sr-debate-label">📊 回测 Agent · 数据视角</div>
              <p className="sr-debate-text">{op.backtest_view}</p>
            </div>
          )}
        </div>
      )}

      {/* Param confirm panel */}
      {paramChanges && paramChanges.length > 0 && (
        <div className="sr-param-confirm">
          <div className="sr-param-confirm-title">建议参数变更</div>
          {paramChanges.map(c => (
            <div key={c.name} className="sr-param-row">
              <span className="sr-param-label">{c.label}</span>
              <span className="sr-param-before">{c.display_current}</span>
              <span className="sr-param-arrow">→</span>
              <span className="sr-param-after">{c.display_proposed}</span>
            </div>
          ))}
          <div className="sr-param-actions">
            <button className="sr-param-apply-btn" onClick={applyParams} disabled={applying}>
              {applying ? "应用中…" : "✓ 确认应用"}
            </button>
            <button className="sr-param-cancel-btn" onClick={cancelParams}>取消</button>
          </div>
        </div>
      )}

      {/* Decision buttons */}
      <div className="sr-iter-approval">
        <button
          className="sr-iter-action-btn sia-adopt"
          onClick={decision === "done" ? undefined : handleAdopt}
          disabled={extracting || decision === "done"}
          style={{
            opacity: decision && decision !== "adopt" && decision !== "done" ? 0.4 : 1,
            fontWeight: decision === "adopt" || decision === "done" ? 700 : 600,
            cursor: decision === "done" ? "default" : undefined,
          }}
        >
          {extracting ? "分析中…" : decision === "done" ? "✅ 已应用" : decision === "adopt" ? "✓ 已选择" : "✓ 采纳"}
        </button>
        <button
          className="sr-iter-action-btn sia-hold"
          onClick={() => decide("hold")}
          style={{ opacity: decision && decision !== "hold" ? 0.4 : 1 }}
        >
          {decision === "hold" ? "○ 待定中" : "○ 待定"}
        </button>
        <button
          className="sr-iter-action-btn sia-reject"
          onClick={() => decide("reject")}
          style={{ opacity: decision && decision !== "reject" ? 0.4 : 1 }}
        >
          {decision === "reject" ? "✕ 已忽略" : "✕ 忽略"}
        </button>
      </div>
    </div>
  );
}


// ── Strategy Notes Panel ──────────────────────────────────────────────────────

// @ts-ignore -- reserved for future strategy notes UI
function _StrategyNotesPanel({ backendOnline }: { backendOnline: boolean }) {
  const [notes, setNotes]     = useState<StrategyNote[]>([]);
  const [history, setHistory] = useState<OverrideHistoryEntry[]>([]);
  const [showHist, setShowHist] = useState(false);

  const loadNotes = useCallback(async () => {
    if (!backendOnline) return;
    try { setNotes(await api.getNotes()); } catch { /* empty */ }
  }, [backendOnline]);

  const loadHistory = async () => {
    if (showHist) { setShowHist(false); return; }
    try {
      setHistory(await api.getOverridesHistory());
      setShowHist(true);
    } catch { setShowHist(true); }
  };

  useEffect(() => { loadNotes(); }, [loadNotes]);

  async function removeNote(id: string) {
    try {
      await api.deleteNote(id);
      setNotes(ns => ns.filter(n => n.id !== id));
    } catch { /* ignore */ }
  }

  const activeNotes = notes.filter(n => n.active);
  if (!backendOnline) return null;

  return (
    <div className="sr-notes-panel">
      <div className="sr-notes-header">
        <h3 className="sr-section-title" style={{ margin: 0 }}>📝 策略记忆</h3>
        <button className="sr-notes-hist-btn" onClick={loadHistory}>
          {showHist ? "▲ 收起" : "参数修改历史 →"}
        </button>
      </div>

      {activeNotes.length === 0 ? (
        <p className="sr-notes-empty">暂无活跃策略记忆。采纳定性迭代建议后自动添加。</p>
      ) : (
        <ul className="sr-notes-list">
          {activeNotes.map(n => (
            <li key={n.id} className="sr-note-item">
              <span className="sr-note-text">{n.text}</span>
              <div className="sr-note-meta">
                {n.source_review_date && <span className="sr-note-date">来自 {n.source_review_date}</span>}
                <button className="sr-note-del" onClick={() => removeNote(n.id)} title="删除">✕</button>
              </div>
            </li>
          ))}
        </ul>
      )}

      {showHist && (
        <div className="sr-history-panel">
          <div className="sr-history-title">参数修改历史</div>
          {history.length === 0 ? (
            <p className="sr-notes-empty">暂无记录。</p>
          ) : (
            history.slice().reverse().map((h, i) => (
              <div key={i} className="sr-history-row">
                <span className="sr-history-date">{new Date(h.changed_at).toLocaleDateString("zh-CN")}</span>
                <span className="sr-history-reason">{h.reason}</span>
                <div className="sr-history-params">
                  {(Object.keys(h.after) as (keyof typeof h.after)[])
                    .filter(k => h.before[k] !== h.after[k] && h.after[k] !== undefined)
                    .map(k => (
                      <span key={k} className="sr-history-change">
                        {k}: {String(h.before[k] ?? "—")} → {String(h.after[k])}
                      </span>
                    ))}
                </div>
              </div>
            ))
          )}
        </div>
      )}
    </div>
  );
}


// ── 3-Agent Debate Panel ──────────────────────────────────────────────────────

const REC_LABEL: Record<string, string> = { ADOPT: "采纳", HOLD: "待定", REJECT: "忽略" };

// @ts-ignore -- reserved for future 3-agent debate UI
function _ThreeAgentDebate({ debate }: { debate: DebateResult }) {
  const tradingView = debate.trading_agent ?? debate.pro;
  const backtestView = debate.backtest_agent ?? debate.con;
  const reviewView = debate.review_agent ?? debate.synthesis;

  return (
    <>
      <div className="sr-debate-side sr-debate-pro">
        <div className="sr-debate-label">📈 交易 Agent (Rex) · 信号视角</div>
        <p className="sr-debate-text">{tradingView}</p>
      </div>
      <div className="sr-debate-side sr-debate-con">
        <div className="sr-debate-label">📊 回测 Agent · 数据视角</div>
        <p className="sr-debate-text">{backtestView}</p>
      </div>
      <div className="sr-debate-synthesis">
        <div className="sr-debate-label">⚖️ 复盘 Agent · 综合结论</div>
        <p className="sr-debate-text">{reviewView}</p>
        <div className="sr-debate-verdict">
          建议：
          <span style={{ color: VERDICT_COLOR[debate.recommendation] ?? "#f59e0b", fontWeight: 700 }}>
            {REC_LABEL[debate.recommendation] ?? debate.recommendation}
          </span>
          <span className="sr-debate-confidence">置信度 {Math.round(debate.confidence * 100)}%</span>
        </div>
      </div>
    </>
  );
}


// ── Per-Stock Debate Panel ────────────────────────────────────────────────────

export function StockDebatePanel({
  symbol, action, context,
}: {
  symbol: string;
  action: "BUY" | "HOLD" | "SELL";
  context: Record<string, unknown>;
}) {
  const [open, setOpen]       = useState(false);
  const [result, setResult]   = useState<StockDebateResult | null>(null);
  const [loading, setLoading] = useState(false);

  async function run() {
    if (result) { setOpen(o => !o); return; }
    setOpen(true);
    setLoading(true);
    try {
      const r = await api.debateStock(symbol, action, context);
      setResult(r);
    } catch { /* silent */ }
    finally { setLoading(false); }
  }

  const verdictColor = result ? (VERDICT_COLOR[result.verdict] ?? "#f59e0b") : undefined;
  const actionLabel  = action === "BUY" ? "买入" : action === "SELL" ? "卖出" : "持有";

  return (
    <div className="stock-debate-wrap">
      <button className="sr-iter-action-btn sia-debate" onClick={run} disabled={loading && !open}>
        {loading ? "⏳ 辩论中…" : result ? (open ? "收起辩论" : "⚡ 查看辩论") : `⚡ Agent 辩论 ${actionLabel}`}
      </button>

      {open && (
        <div className="sr-debate-panel" style={{ marginTop: 8 }}>
          {loading && <div className="sr-debate-loading">3 个 Agent 正在分析 {symbol} 的{actionLabel}决策…</div>}
          {result && (
            <>
              <div className="sr-debate-side sr-debate-pro">
                <div className="sr-debate-label">📈 交易 Agent (Rex)</div>
                <p className="sr-debate-text">{result.trading_agent}</p>
              </div>
              <div className="sr-debate-side sr-debate-con">
                <div className="sr-debate-label">📊 回测 Agent</div>
                <p className="sr-debate-text">{result.backtest_agent}</p>
              </div>
              <div className="sr-debate-synthesis">
                <div className="sr-debate-label">⚖️ 复盘 Agent</div>
                <p className="sr-debate-text">{result.review_agent}</p>
                <div className="sr-debate-verdict">
                  判决：<span style={{ color: verdictColor, fontWeight: 700 }}>{result.verdict}</span>
                  <span className="sr-debate-confidence">置信度 {Math.round(result.confidence * 100)}%</span>
                </div>
                {result.key_risk && (
                  <div className="sr-debate-risk">⚠️ 主要风险：{result.key_risk}</div>
                )}
              </div>
            </>
          )}
        </div>
      )}
    </div>
  );
}
