import { useState, useEffect, useCallback } from "react";
import { api } from "../api/client";
import type { StrategyReview } from "../api/client";

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
    return <div className="brief-offline">Start the backend to view strategy reviews.</div>;
  }

  return (
    <div className="sr-container">
      {/* Header */}
      <div className="sr-header">
        <div>
          <h2 className="sr-title">📈 每日策略复盘</h2>
          <p className="sr-subtitle">收盘后自动生成 · 追踪 15%/月目标进度 · 每日邮件发送</p>
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

      {displayed && <ReviewCard review={displayed} />}
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
            <div key={i} className="sr-iter-card">
              <div className="sr-iter-header">
                <strong className="sr-iter-title">{op.title}</strong>
                <span className="sr-priority-badge" style={{ color: PRIORITY_COLOR[op.priority] ?? "#64748b" }}>
                  {op.priority}
                </span>
              </div>
              <p className="sr-iter-desc">{op.description}</p>
              <span className="sr-iter-impact">预期影响: {op.expected_impact}</span>
            </div>
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
