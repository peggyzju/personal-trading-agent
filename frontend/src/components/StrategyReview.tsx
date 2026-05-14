import { useState, useEffect, useCallback } from "react";
import { api } from "../api/client";
import type { StrategyReview, StrategyIterationOp, DebateResult, ParamChange, ParamExtractResult } from "../api/client";

interface Props { backendOnline: boolean }

const PRIORITY_COLOR: Record<string, string> = {
  HIGH: "#ef4444",
  MEDIUM: "#f59e0b",
  LOW: "#64748b",
};

export function StrategyReviewPanel({ backendOnline }: Props) {
  const [review, setReview] = useState<StrategyReview | null>(null);
  const [history, setHistory] = useState<StrategyReview[]>([]);
  const [generating, setGenerating] = useState(false);
  const [status, setStatus] = useState<string>("");
  const [selectedDate, setSelectedDate] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!backendOnline) return;
    try {
      const r = await api.getStrategyReview();
      if (r && "date" in r) setReview(r as StrategyReview);
      else if ((r as { status?: string })?.status === "running") setStatus("running");
    } catch { /* no review yet */ }

    try {
      const all = await api.getStrategyReviews();
      setHistory(all);
    } catch { /* empty */ }
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
      // Poll until done
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
            load(); // refresh history
          }
        } catch { /* still running */ }
        if (tries > 30) { clearInterval(poll); setGenerating(false); }
      }, 3000);
    } catch {
      setGenerating(false);
      setStatus("");
    }
  }

  const displayed = selectedDate
    ? history.find(r => r.date === selectedDate) ?? review
    : review;

  if (!backendOnline) {
    return <div className="brief-offline">启动后端服务以查看策略复盘。</div>;
  }

  return (
    <div className="sr-container">
      {/* Header */}
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

      {/* History tabs */}
      {history.length > 1 && (
        <div className="sr-date-pills">
          <button
            className={`sr-date-pill${!selectedDate ? " active" : ""}`}
            onClick={() => setSelectedDate(null)}
          >
            最新
          </button>
          {history.slice(0, 7).map(r => (
            <button
              key={r.date}
              className={`sr-date-pill${selectedDate === r.date ? " active" : ""}`}
              onClick={() => setSelectedDate(r.date)}
            >
              {new Date(r.date + "T12:00:00").toLocaleDateString("en-US", { month: "short", day: "numeric" })}
            </button>
          ))}
        </div>
      )}

      {/* No data state */}
      {!displayed && status !== "running" && (
        <div className="brief-empty" style={{ padding: "40px 0" }}>
          <p className="brief-empty-text">
            暂无复盘数据。收盘后（美东 4:15 PM）自动生成，或点击「立即生成」。
          </p>
        </div>
      )}

      {status === "running" && !displayed && (
        <div className="brief-empty" style={{ padding: "40px 0" }}>
          <p className="brief-empty-text">⏳ Claude 正在分析今日交易数据…（约 15-20 秒）</p>
        </div>
      )}

      {displayed && <AutoInsights review={displayed} />}
      {displayed && <ReviewCard review={displayed} />}
    </div>
  );
}

function AutoInsights({ review: r }: { review: StrategyReview }) {
  const perf = r.performance;
  const moodColor = r.market_context.includes("风险") || r.market_context.includes("谨慎") ? "warn" : "good";
  const onTrack = perf.target_gap <= 0;

  const insights: { icon: string; iconClass: string; title: string; detail: string; badge?: string; badgeClass?: string }[] = [
    {
      icon: onTrack ? "✓" : "⚠",
      iconClass: onTrack ? "sri-good" : "sri-warn",
      title: onTrack ? "月度目标: 达标轨道" : "月度目标: 落后进度",
      detail: `当前月收益 ${perf.monthly_return_pct >= 0 ? "+" : ""}${perf.monthly_return_pct.toFixed(2)}%，目标 ${perf.target_monthly_pct}%，差距 ${Math.abs(perf.target_gap).toFixed(1)}%`,
      badge: onTrack ? "OK" : `差 ${perf.target_gap.toFixed(1)}%`,
      badgeClass: onTrack ? "sib-ok" : "sib-medium",
    },
    {
      icon: perf.daily_pl >= 0 ? "↑" : "↓",
      iconClass: perf.daily_pl >= 0 ? "sri-good" : "sri-warn",
      title: `今日 P&L: ${perf.daily_pl >= 0 ? "+" : ""}$${Math.abs(perf.daily_pl).toLocaleString("en-US", { maximumFractionDigits: 0 })}`,
      detail: r.one_line_summary,
      badge: `${perf.daily_return_pct >= 0 ? "+" : ""}${perf.daily_return_pct.toFixed(2)}%`,
      badgeClass: perf.daily_return_pct >= 0 ? "sib-ok" : "sib-medium",
    },
    {
      icon: "🌍",
      iconClass: `sri-${moodColor}`,
      title: "市场背景",
      detail: r.market_context.slice(0, 120) + (r.market_context.length > 120 ? "…" : ""),
    },
    ...(r.what_worked.length > 0 ? [{
      icon: "✓",
      iconClass: "sri-good",
      title: "有效策略",
      detail: r.what_worked.slice(0, 2).join(" · "),
      badge: `${r.what_worked.length} 项`,
      badgeClass: "sib-ok",
    }] : []),
    ...(r.what_didnt.length > 0 ? [{
      icon: "×",
      iconClass: "sri-warn",
      title: "待改进点",
      detail: r.what_didnt.slice(0, 2).join(" · "),
      badge: `${r.what_didnt.length} 项`,
      badgeClass: "sib-medium",
    }] : []),
  ];

  return (
    <div className="sr-auto-insights" style={{ marginBottom: 16 }}>
      <div className="sr-auto-insights-header">
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <span style={{ fontSize: 13 }}>⚡</span>
          <span style={{ fontWeight: 700, fontSize: 13 }}>Vera 自动化洞察</span>
          <span style={{ fontSize: 11, color: "var(--muted)" }}>基于今日交易分析</span>
        </div>
        <span style={{ fontSize: 11, color: "var(--muted)" }}>
          {new Date(r.generated_at).toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" })} 生成
        </span>
      </div>
      {insights.map((ins, i) => (
        <div key={i} className="sr-insight-item">
          <div className={`sr-insight-icon ${ins.iconClass}`}>{ins.icon}</div>
          <div className="sr-insight-body">
            <div className="sr-insight-title">{ins.title}</div>
            <div className="sr-insight-detail">{ins.detail}</div>
          </div>
          {ins.badge && (
            <span className={`sr-insight-badge ${ins.badgeClass ?? ""}`}>{ins.badge}</span>
          )}
        </div>
      ))}
    </div>
  );
}

function ReviewCard({ review: r }: { review: StrategyReview }) {
  const perf = r.performance;
  const dailySign   = perf.daily_pl >= 0 ? "+" : "";
  const monthSign   = perf.monthly_return_pct >= 0 ? "+" : "";
  const plColor     = perf.daily_pl >= 0 ? "#22c55e" : "#ef4444";
  const gapColor    = perf.target_gap <= 0 ? "#22c55e" : perf.target_gap < 5 ? "#f59e0b" : "#ef4444";
  const progressPct = Math.min(100, Math.max(0, (perf.monthly_return_pct / perf.target_monthly_pct) * 100));

  return (
    <div className="sr-card">
      {/* One-line summary */}
      <div className="sr-summary-bar">
        <span className="sr-date-label">
          {new Date(r.date + "T12:00:00").toLocaleDateString("en-US", { weekday: "short", month: "short", day: "numeric" })}
        </span>
        <span className="sr-one-line">{r.one_line_summary}</span>
      </div>

      {/* Performance stats */}
      <div className="sr-perf-row">
        <div className="sr-perf-stat">
          <span className="sr-perf-label">今日 P&L</span>
          <span className="sr-perf-val" style={{ color: plColor }}>
            {dailySign}${Math.abs(perf.daily_pl).toLocaleString("en-US", { maximumFractionDigits: 0 })}
          </span>
          <span className="sr-perf-sub" style={{ color: plColor }}>
            {dailySign}{Math.abs(perf.daily_return_pct).toFixed(2)}%
          </span>
        </div>
        <div className="sr-perf-stat">
          <span className="sr-perf-label">月度收益</span>
          <span className="sr-perf-val">{monthSign}{Math.abs(perf.monthly_return_pct).toFixed(2)}%</span>
          <span className="sr-perf-sub" style={{ color: "#64748b" }}>目标 {perf.target_monthly_pct}%</span>
        </div>
        <div className="sr-perf-stat">
          <span className="sr-perf-label">距目标</span>
          <span className="sr-perf-val" style={{ color: gapColor }}>{perf.target_gap > 0 ? "+" : ""}{perf.target_gap.toFixed(1)}%</span>
          <span className="sr-perf-sub" style={{ color: "#64748b" }}>还需</span>
        </div>
        <div className="sr-perf-stat sr-perf-equity">
          <span className="sr-perf-label">组合总值</span>
          <span className="sr-perf-val">${perf.current_equity.toLocaleString("en-US", { maximumFractionDigits: 0 })}</span>
        </div>
      </div>

      {/* Monthly progress bar */}
      <div className="sr-progress-wrap">
        <div className="sr-progress-track">
          <div
            className="sr-progress-fill"
            style={{
              width: `${progressPct}%`,
              background: progressPct >= 100 ? "#22c55e" : progressPct >= 66 ? "#f59e0b" : "#6366f1",
            }}
          />
          <div className="sr-progress-target" />
        </div>
        <div className="sr-progress-labels">
          <span style={{ color: "#64748b" }}>0%</span>
          <span style={{ color: "#64748b" }}>本月目标 {perf.target_monthly_pct}%</span>
        </div>
      </div>

      {/* Market context */}
      <div className="sr-section">
        <h3 className="sr-section-title">🌍 市场背景</h3>
        <p className="sr-text">{r.market_context}</p>
      </div>

      {/* Strategy assessment */}
      <div className="sr-section">
        <h3 className="sr-section-title">📊 核心策略评估</h3>
        <p className="sr-text">{r.core_strategy_assessment}</p>
        <div className="sr-worked-grid">
          <div>
            <div className="sr-worked-label" style={{ color: "#22c55e" }}>✓ 有效的</div>
            <ul className="sr-bullet-list">
              {r.what_worked.map((w, i) => <li key={i}>{w}</li>)}
            </ul>
          </div>
          <div>
            <div className="sr-worked-label" style={{ color: "#ef4444" }}>✗ 待改进</div>
            <ul className="sr-bullet-list">
              {r.what_didnt.map((w, i) => <li key={i}>{w}</li>)}
            </ul>
          </div>
        </div>
      </div>

      {/* Iteration opportunities */}
      <div className="sr-section">
        <h3 className="sr-section-title">🔁 迭代机会</h3>
        <p className="sr-progress-note">{r.monthly_progress_note}</p>
        <div className="sr-iter-list">
          {r.iteration_opportunities.map((op, i) => (
            <IterCard key={i} op={op} reviewDate={r.date} />
          ))}
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

type IterDecision = "adopt" | "hold" | "reject" | null;

const REC_TO_DECISION: Record<string, IterDecision> = {
  ADOPT: "adopt", HOLD: "hold", REJECT: "reject",
};
const REC_COLOR: Record<string, string> = {
  ADOPT: "#22c55e", HOLD: "#f59e0b", REJECT: "#ef4444",
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
  const [decision, setDecision] = useState<IterDecision>(
    () => readDecisions()[iterKey(reviewDate, op.title)] ?? null
  );
  const [showDebate, setShowDebate] = useState(false);
  const [debate, setDebate]         = useState<DebateResult | null>(null);
  const [debating, setDebating]     = useState(false);

  // Param-confirm flow
  const [extracting, setExtracting]           = useState(false);
  const [paramResult, setParamResult]         = useState<ParamExtractResult | null>(null);
  const [showConfirm, setShowConfirm]         = useState(false);
  const [savingOverrides, setSavingOverrides] = useState(false);

  function decide(d: IterDecision) {
    setDecision(d);
    saveDecision(reviewDate, op.title, d);
  }

  async function handleDebate() {
    setShowDebate(s => !s);
    if (debate || debating) return;
    setDebating(true);
    try {
      const result = await api.debateIteration(op);
      setDebate(result);
      if (!decision) decide(REC_TO_DECISION[result.recommendation] ?? null);
    } catch { /* ignore */ }
    finally { setDebating(false); }
  }

  async function handleAdoptClick() {
    if (decision === "adopt") {
      // Toggle off — just clear
      decide(null);
      setShowConfirm(false);
      setParamResult(null);
      return;
    }
    // Extract params first
    setExtracting(true);
    setShowConfirm(false);
    try {
      const result = await api.extractParams(op);
      setParamResult(result);
      setShowConfirm(true);
    } catch {
      // Fallback: no params extracted, confirm with no changes
      setParamResult({ mappable: false, note: "无法连接后端，仅记录决策。", params: [] });
      setShowConfirm(true);
    } finally {
      setExtracting(false);
    }
  }

  async function confirmAdopt() {
    if (!paramResult) return;
    setSavingOverrides(true);
    try {
      if (paramResult.mappable && paramResult.params.length > 0) {
        const patch: Record<string, number | string> = { reason: `采纳: ${op.title}` };
        for (const p of paramResult.params) patch[p.name] = p.proposed;
        await api.saveOverrides(patch);
      }
      decide("adopt");
      setShowConfirm(false);
    } catch { /* still mark decision even if save failed */ decide("adopt"); setShowConfirm(false); }
    finally { setSavingOverrides(false); }
  }

  const cardCls = `sr-iter-card${decision === "adopt" ? " sr-iter-adopted" : decision === "reject" ? " sr-iter-rejected" : ""}`;

  return (
    <div className={cardCls}>
      <div className="sr-iter-header">
        <strong className="sr-iter-title">{op.title}</strong>
        <span className="sr-priority-badge" style={{ color: PRIORITY_COLOR[op.priority] ?? "#64748b" }}>
          {op.priority}
        </span>
      </div>
      <p className="sr-iter-desc">{op.description}</p>
      <span className="sr-iter-impact">预期影响: {op.expected_impact}</span>

      <div className="sr-iter-approval">
        <button className="sr-iter-action-btn sia-adopt"
          onClick={handleAdoptClick}
          disabled={extracting}
          style={{ opacity: decision && decision !== "adopt" ? 0.4 : 1, fontWeight: decision === "adopt" ? 700 : 600 }}>
          {extracting ? "⏳ 解析中…" : decision === "adopt" ? "✓ 已采纳" : "✓ 采纳"}
        </button>
        <button className="sr-iter-action-btn sia-hold"
          onClick={() => decide(decision === "hold" ? null : "hold")}
          style={{ opacity: decision && decision !== "hold" ? 0.4 : 1 }}>
          {decision === "hold" ? "○ 待定中" : "○ 待定"}
        </button>
        <button className="sr-iter-action-btn sia-reject"
          onClick={() => decide(decision === "reject" ? null : "reject")}
          style={{ opacity: decision && decision !== "reject" ? 0.4 : 1 }}>
          {decision === "reject" ? "✕ 已忽略" : "✕ 忽略"}
        </button>
        <button
          className={`sr-iter-action-btn sia-debate${showDebate ? " active" : ""}`}
          onClick={handleDebate}
          disabled={debating && !showDebate}
        >
          {debating ? "⏳ 辩论中…" : "⚡ Agent 辩论"}
        </button>
      </div>

      {/* Parameter confirmation panel */}
      {showConfirm && paramResult && (
        <div className="sr-param-confirm">
          <div className="sr-param-confirm-title">
            确认应用参数变更到 Agent？
          </div>
          {paramResult.mappable && paramResult.params.length > 0 ? (
            <table className="sr-param-table">
              <thead>
                <tr>
                  <th>参数</th>
                  <th>当前值</th>
                  <th>→</th>
                  <th>新值</th>
                </tr>
              </thead>
              <tbody>
                {paramResult.params.map(p => (
                  <tr key={p.name}>
                    <td className="sr-param-label">{p.label}</td>
                    <td className="sr-param-cur">{p.display_current}</td>
                    <td className="sr-param-arrow">→</td>
                    <td className="sr-param-new">{p.display_proposed}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <p className="sr-param-note">{paramResult.note || "此建议不涉及数值参数变更，仅记录决策。"}</p>
          )}
          <div className="sr-param-actions">
            <button
              className="sr-param-confirm-btn"
              onClick={confirmAdopt}
              disabled={savingOverrides}
            >
              {savingOverrides ? "保存中…" : "确认应用"}
            </button>
            <button
              className="sr-param-cancel-btn"
              onClick={() => setShowConfirm(false)}
            >
              取消
            </button>
          </div>
        </div>
      )}

      {showDebate && (
        <div className="sr-debate-panel">
          {debating && !debate && (
            <div className="sr-debate-loading">Agent Alpha 与 Beta 正在辩论中，约 5 秒…</div>
          )}
          {debate && (
            <>
              <div className="sr-debate-side sr-debate-pro">
                <div className="sr-debate-label">🟢 Agent Alpha · 赞成</div>
                <p className="sr-debate-text">{debate.pro}</p>
              </div>
              <div className="sr-debate-side sr-debate-con">
                <div className="sr-debate-label">🔴 Agent Beta · 反对</div>
                <p className="sr-debate-text">{debate.con}</p>
              </div>
              <div className="sr-debate-synthesis">
                <div className="sr-debate-label">⚖️ 综合结论</div>
                <p className="sr-debate-text">{debate.synthesis}</p>
                <div className="sr-debate-verdict">
                  建议：
                  <span style={{ color: REC_COLOR[debate.recommendation], fontWeight: 700 }}>
                    {debate.recommendation === "ADOPT" ? "采纳" : debate.recommendation === "HOLD" ? "待定" : "忽略"}
                  </span>
                  <span className="sr-debate-confidence">置信度 {Math.round(debate.confidence * 100)}%</span>
                </div>
              </div>
            </>
          )}
        </div>
      )}
    </div>
  );
}
