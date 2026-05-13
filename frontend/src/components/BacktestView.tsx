import { useState, useEffect } from "react";
import { api } from "../api/client";
import type { BacktestResult, BacktestTrade } from "../api/client";

interface Props { backendOnline: boolean }

export function BacktestView({ backendOnline }: Props) {
  const [data, setData] = useState<BacktestResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [holdDays, setHoldDays] = useState(10);
  const [targetPct, setTargetPct] = useState(8);
  const [period, setPeriod] = useState<"1y" | "2y">("2y");

  useEffect(() => {
    if (backendOnline) load();
  }, [backendOnline]);

  async function load() {
    try {
      const result = await api.getBacktest();
      if (result.status !== "not_run") setData(result);
    } catch { /* empty */ }
  }

  async function run() {
    setLoading(true);
    try {
      await api.triggerBacktest({ hold_days: holdDays, target_pct: targetPct / 100, period });
      const poll = setInterval(async () => {
        const result = await api.getBacktest();
        setData(result);
        if (result.status !== "running") {
          clearInterval(poll);
          setLoading(false);
        }
      }, 3000);
      setTimeout(() => { clearInterval(poll); setLoading(false); }, 120_000);
    } catch {
      setLoading(false);
    }
  }

  if (!backendOnline) {
    return <div className="brief-offline">启动后端服务以运行回测。</div>;
  }

  return (
    <div className="backtest-container">
      <div className="scan-header">
        <div>
          <h2>📊 策略回测</h2>
          <span className="scan-meta">
            基于自选股 + 扫描候选股做前向模拟 · 无未来数据泄漏
          </span>
        </div>
      </div>

      {/* Config row */}
      <div className="backtest-config">
        <label className="config-label">
          持仓天数
          <input
            type="number" min={3} max={30} value={holdDays}
            onChange={(e) => setHoldDays(Number(e.target.value))}
            className="config-input"
          />
        </label>
        <label className="config-label">
          目标涨幅 (%)
          <input
            type="number" min={2} max={30} value={targetPct}
            onChange={(e) => setTargetPct(Number(e.target.value))}
            className="config-input"
          />
        </label>
        <label className="config-label">
          回测周期
          <select value={period} onChange={(e) => setPeriod(e.target.value as "1y" | "2y")} className="config-input">
            <option value="1y">1 年</option>
            <option value="2y">2 年</option>
          </select>
        </label>
        <button className="brief-generate-btn" onClick={run} disabled={loading} style={{ alignSelf: "flex-end" }}>
          {loading ? "运行中…" : "▶ 运行回测"}
        </button>
      </div>

      {data?.status === "running" && (
        <div className="scan-running">
          <div className="scan-spinner" />
          <p>正在下载历史数据并模拟交易…</p>
          <p className="brief-disclaimer">通常需要 15–30 秒</p>
        </div>
      )}

      {data?.status === "error" && (
        <div className="brief-empty">
          <p className="error-text">回测失败：{data.error}</p>
          <button className="brief-generate-btn" onClick={run}>重试</button>
        </div>
      )}

      {data?.status === "done" && (
        <>
          <BacktestStats data={data} />
          {data.equity_curve && <EquityCurve curve={data.equity_curve} />}
          {data.trades && <TradesTable trades={data.trades} />}
        </>
      )}

      {(!data || data.status === "not_run") && !loading && (
        <div className="brief-empty" style={{ paddingTop: 40 }}>
          <p className="brief-empty-text">
            调整上方参数后点击「运行回测」，将使用自选股 + 最新扫描候选股进行模拟。
          </p>
        </div>
      )}
    </div>
  );
}

function BacktestStats({ data }: { data: BacktestResult }) {
  const alphaColor = (data.alpha_pct ?? 0) >= 0 ? "#22c55e" : "#ef4444";
  const ddColor = (data.max_drawdown_pct ?? 0) > 15 ? "#ef4444" : (data.max_drawdown_pct ?? 0) > 8 ? "#f59e0b" : "#22c55e";
  const sharpeColor = (data.sharpe_ratio ?? 0) >= 1.5 ? "#22c55e" : (data.sharpe_ratio ?? 0) >= 0.8 ? "#f59e0b" : "#ef4444";

  const breakdown = data.exit_breakdown ?? {};

  return (
    <div className="backtest-stats-section">
      <div className="backtest-kpi-grid">
        <KPI label="总交易次数" value={String(data.total_trades)} />
        <KPI label="胜率" value={`${data.win_rate}%`}
          color={(data.win_rate ?? 0) >= 55 ? "#22c55e" : (data.win_rate ?? 0) >= 45 ? "#f59e0b" : "#ef4444"} />
        <KPI label="平均盈利" value={`+${data.avg_win_pct}%`} color="#22c55e" />
        <KPI label="平均亏损" value={`${data.avg_loss_pct}%`} color="#ef4444" />
        <KPI label="盈亏比" value={String(data.profit_factor)}
          color={(data.profit_factor ?? 0) >= 1.5 ? "#22c55e" : "#f59e0b"} />
        <KPI label="策略收益" value={`${(data.total_return_pct ?? 0) >= 0 ? "+" : ""}${data.total_return_pct ?? 0}%`}
          color={(data.total_return_pct ?? 0) >= 0 ? "#22c55e" : "#ef4444"} />
        <KPI label="SPY 买入持有" value={`${(data.spy_return_pct ?? 0) >= 0 ? "+" : ""}${data.spy_return_pct ?? 0}%`} />
        <KPI label="超额收益" value={`${(data.alpha_pct ?? 0) >= 0 ? "+" : ""}${data.alpha_pct ?? 0}%`} color={alphaColor} />
        <KPI label="最大回撤" value={`-${data.max_drawdown_pct ?? 0}%`} color={ddColor} />
        <KPI label="夏普比率" value={String(data.sharpe_ratio)} color={sharpeColor} />
      </div>

      {Object.keys(breakdown).length > 0 && (
        <div className="exit-breakdown">
          <span className="holding-label">离场原因：</span>
          {Object.entries(breakdown).map(([reason, count]) => (
            <span key={reason} className="exit-tag">
              {reason.replace("_", " ")} ({count})
            </span>
          ))}
          {data.params && (
            <span className="exit-tag" style={{ color: "#64748b" }}>
              {data.params.hold_days}d hold · {(data.params.target_pct * 100).toFixed(0)}% target · {data.params.period}
            </span>
          )}
        </div>
      )}
    </div>
  );
}

function KPI({ label, value, color }: { label: string; value: string; color?: string }) {
  return (
    <div className="backtest-kpi">
      <span className="holding-label">{label}</span>
      <span className="backtest-kpi-value" style={color ? { color } : undefined}>{value}</span>
    </div>
  );
}

function EquityCurve({ curve }: { curve: number[] }) {
  if (!curve || curve.length < 2) return null;

  const min = Math.min(...curve);
  const max = Math.max(...curve);
  const range = max - min || 1;
  const w = 800;
  const h = 120;
  const pad = 8;

  const points = curve.map((v, i) => {
    const x = pad + (i / (curve.length - 1)) * (w - 2 * pad);
    const y = h - pad - ((v - min) / range) * (h - 2 * pad);
    return `${x},${y}`;
  }).join(" ");

  const finalReturn = curve[curve.length - 1] - 100;
  const lineColor = finalReturn >= 0 ? "#22c55e" : "#ef4444";
  const baseline_y = h - pad - ((100 - min) / range) * (h - 2 * pad);

  return (
    <div className="equity-curve-section">
      <h3 className="backtest-section-title">资金曲线（初始 $100）</h3>
      <div className="equity-curve-wrap">
        <svg viewBox={`0 0 ${w} ${h}`} className="equity-svg" preserveAspectRatio="none">
          {/* Baseline at 100 */}
          <line x1={pad} y1={baseline_y} x2={w - pad} y2={baseline_y}
            stroke="#2a2d3a" strokeWidth="1" strokeDasharray="4,4" />
          {/* Fill */}
          <polygon
            points={`${pad},${h - pad} ${points} ${w - pad},${h - pad}`}
            fill={lineColor} opacity="0.08"
          />
          {/* Line */}
          <polyline points={points} fill="none" stroke={lineColor} strokeWidth="2" />
        </svg>
        <div className="equity-labels">
          <span style={{ color: lineColor }}>${curve[curve.length - 1].toFixed(0)}</span>
          <span className="scan-meta">${curve[0].toFixed(0)}</span>
        </div>
      </div>
    </div>
  );
}

function TradesTable({ trades }: { trades: BacktestTrade[] }) {
  const [show, setShow] = useState(20);
  if (!trades || trades.length === 0) return null;

  return (
    <div className="backtest-trades-section">
      <h3 className="backtest-section-title">交易记录（共 {trades.length} 笔）</h3>
      <div className="positions-table-wrap">
        <table className="positions-table">
          <thead>
            <tr>
              <th>股票</th>
              <th>买入日</th>
              <th>卖出日</th>
              <th>持仓天</th>
              <th>买入价</th>
              <th>卖出价</th>
              <th>盈亏 %</th>
              <th>离场原因</th>
            </tr>
          </thead>
          <tbody>
            {trades.slice(0, show).map((t, i) => (
              <tr key={i}>
                <td><strong>{t.symbol}</strong></td>
                <td>{t.entry_date}</td>
                <td>{t.exit_date}</td>
                <td>{t.days_held}</td>
                <td>${t.entry_price}</td>
                <td>${t.exit_price}</td>
                <td className={t.pnl_pct >= 0 ? "up" : "down"}>
                  {t.pnl_pct >= 0 ? "+" : ""}{t.pnl_pct}%
                </td>
                <td><span className={`exit-tag exit-${t.exit_reason}`}>{t.exit_reason?.replace("_", " ")}</span></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {trades.length > show && (
        <button className="brief-regenerate-btn" onClick={() => setShow(trades.length)} style={{ marginTop: 8 }}>
          显示全部 {trades.length} 笔
        </button>
      )}
    </div>
  );
}
