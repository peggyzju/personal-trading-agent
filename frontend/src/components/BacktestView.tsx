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
    return <div className="brief-offline">Start the backend to run backtests.</div>;
  }

  return (
    <div className="backtest-container">
      <div className="scan-header">
        <div>
          <h2>📊 Backtesting</h2>
          <span className="scan-meta">
            Walk-forward simulation on watchlist + scan candidates · no lookahead bias
          </span>
        </div>
      </div>

      {/* Config row */}
      <div className="backtest-config">
        <label className="config-label">
          Hold (days)
          <input
            type="number" min={3} max={30} value={holdDays}
            onChange={(e) => setHoldDays(Number(e.target.value))}
            className="config-input"
          />
        </label>
        <label className="config-label">
          Target (%)
          <input
            type="number" min={2} max={30} value={targetPct}
            onChange={(e) => setTargetPct(Number(e.target.value))}
            className="config-input"
          />
        </label>
        <label className="config-label">
          Period
          <select value={period} onChange={(e) => setPeriod(e.target.value as "1y" | "2y")} className="config-input">
            <option value="1y">1 year</option>
            <option value="2y">2 years</option>
          </select>
        </label>
        <button className="brief-generate-btn" onClick={run} disabled={loading} style={{ alignSelf: "flex-end" }}>
          {loading ? "Running…" : "▶ Run Backtest"}
        </button>
      </div>

      {data?.status === "running" && (
        <div className="scan-running">
          <div className="scan-spinner" />
          <p>Downloading historical data and simulating trades…</p>
          <p className="brief-disclaimer">Usually takes 15–30 seconds</p>
        </div>
      )}

      {data?.status === "error" && (
        <div className="brief-empty">
          <p className="error-text">Backtest failed: {data.error}</p>
          <button className="brief-generate-btn" onClick={run}>Retry</button>
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
            Configure parameters above and run the backtest. Uses your watchlist + latest scan candidates.
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
        <KPI label="Total Trades" value={String(data.total_trades)} />
        <KPI label="Win Rate" value={`${data.win_rate}%`}
          color={(data.win_rate ?? 0) >= 55 ? "#22c55e" : (data.win_rate ?? 0) >= 45 ? "#f59e0b" : "#ef4444"} />
        <KPI label="Avg Win" value={`+${data.avg_win_pct}%`} color="#22c55e" />
        <KPI label="Avg Loss" value={`${data.avg_loss_pct}%`} color="#ef4444" />
        <KPI label="Profit Factor" value={String(data.profit_factor)}
          color={(data.profit_factor ?? 0) >= 1.5 ? "#22c55e" : "#f59e0b"} />
        <KPI label="Strategy Return" value={`${(data.total_return_pct ?? 0) >= 0 ? "+" : ""}${data.total_return_pct ?? 0}%`}
          color={(data.total_return_pct ?? 0) >= 0 ? "#22c55e" : "#ef4444"} />
        <KPI label="SPY Buy&Hold" value={`${(data.spy_return_pct ?? 0) >= 0 ? "+" : ""}${data.spy_return_pct}%`} />
        <KPI label="Alpha vs SPY" value={`${(data.alpha_pct ?? 0) >= 0 ? "+" : ""}${data.alpha_pct}%`} color={alphaColor} />
        <KPI label="Max Drawdown" value={`-${data.max_drawdown_pct}%`} color={ddColor} />
        <KPI label="Sharpe Ratio" value={String(data.sharpe_ratio)} color={sharpeColor} />
      </div>

      {Object.keys(breakdown).length > 0 && (
        <div className="exit-breakdown">
          <span className="holding-label">Exit reasons: </span>
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
      <h3 className="backtest-section-title">Equity Curve (start = $100)</h3>
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
      <h3 className="backtest-section-title">Trade History ({trades.length} total)</h3>
      <div className="positions-table-wrap">
        <table className="positions-table">
          <thead>
            <tr>
              <th>Symbol</th>
              <th>Entry</th>
              <th>Exit</th>
              <th>Days</th>
              <th>Entry $</th>
              <th>Exit $</th>
              <th>P&L %</th>
              <th>Exit Reason</th>
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
          Show all {trades.length} trades
        </button>
      )}
    </div>
  );
}
