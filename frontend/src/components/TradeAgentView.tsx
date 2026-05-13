import { useState, useEffect, useCallback } from "react";
import { api } from "../api/client";
import type { PendingTrade, AgentState } from "../api/client";

const SOURCE_LABEL: Record<string, string> = {
  scanner: "🔍 Scanner",
  watchlist: "📋 Watchlist",
  holdings: "📉 Holdings",
};

const STATUS_COLOR: Record<string, string> = {
  pending:  "#f59e0b",
  executed: "#22c55e",
  rejected: "#64748b",
  expired:  "#475569",
  error:    "#ef4444",
};

interface Props { backendOnline: boolean }

export function TradeAgentView({ backendOnline }: Props) {
  const [state, setState] = useState<AgentState | null>(null);
  const [running, setRunning] = useState(false);

  const load = useCallback(async () => {
    try {
      const s = await api.getAgentState();
      setState(s);
    } catch { /* backend offline */ }
  }, []);

  useEffect(() => {
    if (backendOnline) load();
    const id = setInterval(() => { if (backendOnline) load(); }, 15_000);
    return () => clearInterval(id);
  }, [backendOnline, load]);

  async function handleRun() {
    setRunning(true);
    try {
      await api.runAgent();
      // Poll until new log entry appears
      let tries = 0;
      const poll = setInterval(async () => {
        await load();
        tries++;
        if (tries > 20) { clearInterval(poll); setRunning(false); }
      }, 2000);
      setTimeout(() => { clearInterval(poll); setRunning(false); }, 60_000);
    } catch {
      setRunning(false);
    }
  }

  async function handleApprove(id: string) {
    try {
      const updated = await api.approveTrade(id);
      setState(prev => prev ? {
        ...prev,
        trades: prev.trades.map(t => t.id === id ? updated : t),
      } : prev);
    } catch (e: unknown) {
      alert(e instanceof Error ? e.message : "Approve failed");
    }
  }

  async function handleReject(id: string) {
    try {
      const updated = await api.rejectTrade(id);
      setState(prev => prev ? {
        ...prev,
        trades: prev.trades.map(t => t.id === id ? updated : t),
      } : prev);
    } catch (e: unknown) {
      alert(e instanceof Error ? e.message : "Reject failed");
    }
  }

  if (!backendOnline) {
    return <div className="brief-offline">Start the backend to use the Trade Agent.</div>;
  }

  const pending = state?.trades.filter(t => t.status === "pending") ?? [];
  const history = state?.trades.filter(t => t.status !== "pending") ?? [];
  const lastRun = state?.log[0];

  return (
    <div className="agent-container">
      {/* Header */}
      <div className="scan-header">
        <div>
          <h2>🤖 Trade Agent</h2>
          <span className="scan-meta">
            自动识别信号 → 等待确认 → 执行
          </span>
          {lastRun && (
            <span className="scan-meta">
              {" · 上次运行: "}{new Date(lastRun.run_at + (lastRun.run_at.endsWith("Z") ? "" : "Z")).toLocaleTimeString()}
              {" · "}{lastRun.trades_queued} 个信号入队
            </span>
          )}
        </div>
        <button className="brief-generate-btn" onClick={handleRun} disabled={running}>
          {running ? "扫描中…" : "▶ 立即扫描"}
        </button>
      </div>

      {/* Pending trades */}
      <div className="agent-section">
        <h3 className="backtest-section-title">
          待确认 ({pending.length})
        </h3>

        {pending.length === 0 ? (
          <div className="brief-empty" style={{ padding: "24px 0" }}>
            <p className="brief-empty-text">暂无待确认的交易信号。点击「立即扫描」触发分析。</p>
          </div>
        ) : (
          <div className="pending-trades-list">
            {pending.map(t => (
              <PendingCard
                key={t.id}
                trade={t}
                onApprove={() => handleApprove(t.id)}
                onReject={() => handleReject(t.id)}
              />
            ))}
          </div>
        )}
      </div>

      {/* History */}
      {history.length > 0 && (
        <div className="agent-section">
          <h3 className="backtest-section-title">历史记录</h3>
          <div className="positions-table-wrap">
            <table className="positions-table">
              <thead>
                <tr>
                  <th>时间</th>
                  <th>来源</th>
                  <th>信号</th>
                  <th>股票</th>
                  <th>方向</th>
                  <th>金额</th>
                  <th>状态</th>
                  <th>Order ID</th>
                </tr>
              </thead>
              <tbody>
                {history.map(t => (
                  <tr key={t.id} style={{ opacity: t.status === "expired" ? 0.5 : 1 }}>
                    <td style={{ fontSize: 11, color: "var(--muted)" }}>
                      {new Date(t.created_at + (t.created_at.endsWith("Z") ? "" : "Z")).toLocaleTimeString()}
                    </td>
                    <td style={{ fontSize: 11 }}>{SOURCE_LABEL[t.source] ?? t.source}</td>
                    <td>
                      <span className="signal-badge" style={{ background: t.side === "buy" ? "#16a34a" : "#ef4444", fontSize: 11, padding: "2px 6px" }}>
                        {t.signal}
                      </span>
                    </td>
                    <td><strong>{t.symbol}</strong></td>
                    <td className={t.side === "buy" ? "up" : "down"}>{t.side.toUpperCase()}</td>
                    <td>{t.notional ? `$${t.notional.toFixed(0)}` : t.qty ? `${t.qty} 股` : "—"}</td>
                    <td>
                      <span style={{ color: STATUS_COLOR[t.status] ?? "var(--muted)", fontSize: 12, fontWeight: 600 }}>
                        {t.status}
                      </span>
                    </td>
                    <td style={{ fontSize: 11, color: "var(--muted)" }}>
                      {t.executed_order_id ? t.executed_order_id.slice(0, 8) + "…" : t.error ?? "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* Run log */}
      {(state?.log.length ?? 0) > 0 && (
        <div className="agent-section">
          <h3 className="backtest-section-title">运行日志</h3>
          <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
            {state!.log.map((l, i) => (
              <div key={i} className="agent-log-row">
                <span style={{ color: "var(--muted)", fontSize: 11 }}>
                  {new Date(l.run_at + (l.run_at.endsWith("Z") ? "" : "Z")).toLocaleTimeString()}
                </span>
                <span style={{ fontSize: 12 }}>
                  发现 <strong>{l.signals_found}</strong> 个信号，入队 <strong>{l.trades_queued}</strong> 笔
                </span>
                {l.sources.length > 0 && (
                  <span style={{ color: "var(--muted)", fontSize: 11 }}>
                    来源: {l.sources.join(", ")}
                  </span>
                )}
                {l.status === "error" && (
                  <span style={{ color: "#ef4444", fontSize: 11 }}>{l.error}</span>
                )}
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function PendingCard({
  trade: t,
  onApprove,
  onReject,
}: {
  trade: PendingTrade;
  onApprove: () => void;
  onReject: () => void;
}) {
  const [confirming, setConfirming] = useState(false);
  const expiresIn = Math.max(
    0,
    Math.round((new Date(t.expires_at).getTime() - Date.now()) / 60000)
  );

  return (
    <div className="pending-card">
      <div className="pending-card-top">
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <span className={`pending-side ${t.side === "buy" ? "up" : "down"}`}>
            {t.side === "buy" ? "▲ BUY" : "▼ SELL"}
          </span>
          <span className="symbol" style={{ fontSize: 18 }}>{t.symbol}</span>
          <span className="signal-badge" style={{
            background: t.side === "buy" ? "#16a34a" : "#ef4444",
            fontSize: 12, padding: "2px 8px",
          }}>
            {t.signal}
          </span>
          <span style={{ color: "var(--muted)", fontSize: 12 }}>
            {SOURCE_LABEL[t.source] ?? t.source}
          </span>
        </div>
        <div style={{ display: "flex", gap: 6, alignItems: "center" }}>
          <span style={{ color: "var(--muted)", fontSize: 11 }}>
            {expiresIn}分钟后过期
          </span>
          {confirming ? (
            <>
              <button className="trade-btn buy-btn" style={{ width: "auto", margin: 0, padding: "6px 16px" }} onClick={onApprove}>
                ✓ 确认执行
              </button>
              <button className="cancel-small-btn" onClick={() => setConfirming(false)}>取消</button>
            </>
          ) : (
            <>
              <button
                className="trade-btn buy-btn"
                style={{ width: "auto", margin: 0, padding: "6px 14px", background: t.side === "buy" ? "#16a34a" : "#ef4444" }}
                onClick={() => setConfirming(true)}
              >
                Approve
              </button>
              <button className="cancel-small-btn" onClick={onReject}>Reject</button>
            </>
          )}
        </div>
      </div>

      <div className="pending-card-body">
        <div className="pending-stats">
          {t.price && <StatItem label="当前价格" value={`$${t.price.toFixed(2)}`} />}
          {t.notional && <StatItem label="买入金额" value={`$${t.notional.toFixed(0)}`} />}
          {t.qty && <StatItem label="股数" value={`${t.qty} 股`} />}
          {t.stop_loss && <StatItem label="止损" value={`$${t.stop_loss.toFixed(2)}`} color="#ef4444" />}
          {t.target_price && <StatItem label="目标" value={`$${t.target_price.toFixed(2)}`} color="#22c55e" />}
          <StatItem label="置信度" value={`${(t.confidence * 100).toFixed(0)}%`}
            color={t.confidence >= 0.8 ? "#22c55e" : t.confidence >= 0.6 ? "#f59e0b" : "#ef4444"} />
        </div>
        <p style={{ fontSize: 13, color: "var(--muted)", marginTop: 8, lineHeight: 1.5 }}>{t.reason}</p>
      </div>
    </div>
  );
}

function StatItem({ label, value, color }: { label: string; value: string; color?: string }) {
  return (
    <div className="tech-stat">
      <span className="tech-label">{label}</span>
      <span className="tech-value" style={color ? { color } : undefined}>{value}</span>
    </div>
  );
}
