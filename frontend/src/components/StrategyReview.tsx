import { useState } from "react";
import { api } from "../api/client";
import type { StockDebateResult, PostmortemResult, PostmortemTrade } from "../api/client";

interface Props { backendOnline: boolean }

export function StrategyReviewPanel({ backendOnline }: Props) {
  if (!backendOnline) {
    return <div className="brief-offline">启动后端服务以查看策略复盘。</div>;
  }

  return (
    <div className="sr-container">
      <PostMortemPanel backendOnline={backendOnline} />
    </div>
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


const VERDICT_COLOR: Record<string, string> = {
  STRONG_BUY: "#22c55e", BUY: "#86efac",
  SELL: "#f87171", STRONG_SELL: "#ef4444", AVOID: "#ef4444",
};

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
