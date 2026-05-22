import { useState, useEffect } from "react";
import { api } from "../api/client";
import type { VersionCompareResult, VersionStats, BacktestResult } from "../api/client";

interface Props { backendOnline: boolean }

export function BacktestView({ backendOnline }: Props) {
  const [data, setData] = useState<VersionCompareResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [period, setPeriod] = useState<"6mo" | "1y">("6mo");
  const [holdDays, setHoldDays] = useState(7);

  useEffect(() => {
    if (backendOnline) {
      api.getVersionCompare().then(r => { if (r.status !== "not_run") setData(r); }).catch(() => {});
    }
  }, [backendOnline]);

  async function run() {
    setLoading(true);
    try {
      await api.triggerVersionCompare({ period, hold_days: holdDays });
      const poll = setInterval(async () => {
        const r = await api.getVersionCompare();
        setData(r);
        if (r.status !== "running") { clearInterval(poll); setLoading(false); }
      }, 3000);
      setTimeout(() => { clearInterval(poll); setLoading(false); }, 180_000);
    } catch { setLoading(false); }
  }

  if (!backendOnline) {
    return <div className="brief-offline">启动后端服务以运行回测。</div>;
  }

  const vMeta = data?.versions_meta ?? [];
  const prevMeta = vMeta[0];
  const currMeta = vMeta[1];

  return (
    <div className="backtest-container">
      <div className="scan-header">
        <div>
          <h2>📊 版本对比回测</h2>
          <span className="scan-meta">
            v_prev vs v_current · 相同时间窗口 · 纯技术信号 · ATR 定仓 · 无未来数据泄漏
          </span>
        </div>
      </div>

      {/* Config */}
      <div className="backtest-config">
        <label className="config-label">
          回测周期
          <select value={period} onChange={e => setPeriod(e.target.value as "6mo" | "1y")} className="config-input">
            <option value="6mo">6 个月</option>
            <option value="1y">1 年</option>
          </select>
        </label>
        <label className="config-label">
          持仓天数
          <input type="number" min={5} max={20} value={holdDays}
            onChange={e => setHoldDays(Number(e.target.value))} className="config-input" />
        </label>
        <button className="brief-generate-btn" onClick={run} disabled={loading} style={{ alignSelf: "flex-end" }}>
          {loading ? "运行中…" : "▶ 运行版本对比"}
        </button>
      </div>

      {/* Version labels */}
      {vMeta.length === 2 && (
        <div className="version-labels-row">
          <VersionTag meta={prevMeta} side="prev" />
          <VersionTag meta={currMeta} side="current" />
        </div>
      )}

      {/* Running */}
      {(loading || data?.status === "running") && (
        <div className="scan-running">
          <div className="scan-spinner" />
          <p>正在下载历史数据并对比两个版本…</p>
          <p className="brief-disclaimer">通常需要 30–60 秒</p>
        </div>
      )}

      {/* Error */}
      {data?.status === "error" && (
        <div className="brief-empty">
          <p className="error-text">回测失败：{data.error}</p>
          <button className="brief-generate-btn" onClick={run}>重试</button>
        </div>
      )}

      {/* Results */}
      {data?.status === "done" && data.v_prev && data.v_current && (
        <>
          <SPYBenchmark spy={data.spy_return_pct ?? 0} />
          <CompareTable prev={data.v_prev} curr={data.v_current} />
          <div className="version-curves-row">
            <MiniCurve label={data.v_prev.label} curve={data.v_prev.stats.equity_curve} />
            <MiniCurve label={data.v_current.label} curve={data.v_current.stats.equity_curve} />
          </div>
        </>
      )}

      {(!data || data.status === "not_run") && !loading && (
        <div className="brief-empty" style={{ paddingTop: 40 }}>
          <p className="brief-empty-text">点击「运行版本对比」，将使用自选股 + 最新扫描候选股对比 v_prev vs v_current。</p>
        </div>
      )}
    </div>
  );
}

// ── Version tag ───────────────────────────────────────────────────────────────

function VersionTag({ meta, side }: { meta: { version: string; label: string; description: string; created_at: string; changes: string[] }; side: "prev" | "current" }) {
  const isNew = side === "current";
  return (
    <div className={`version-tag-card ${isNew ? "version-tag-current" : "version-tag-prev"}`}>
      <div className="version-tag-header">
        <span className="version-badge">{meta.version}</span>
        <span className="version-date">{meta.created_at}</span>
        {isNew && <span className="version-new-chip">当前版本</span>}
      </div>
      <p className="version-description">{meta.description}</p>
      {meta.changes.length > 0 && (
        <div className="version-changes">
          {meta.changes.map((c, i) => <span key={i} className="version-change-tag">{c}</span>)}
        </div>
      )}
    </div>
  );
}

// ── SPY benchmark ─────────────────────────────────────────────────────────────

function SPYBenchmark({ spy }: { spy: number }) {
  return (
    <div className="version-spy-row">
      <span className="holding-label">SPY 同期基准</span>
      <span style={{ color: spy >= 0 ? "#22c55e" : "#ef4444", fontWeight: 600 }}>
        {spy >= 0 ? "+" : ""}{spy}%
      </span>
    </div>
  );
}

// ── Side-by-side comparison table ────────────────────────────────────────────

const METRICS: { key: keyof BacktestResult; label: string; fmt: (v: number) => string; better: "higher" | "lower"; primary?: boolean }[] = [
  { key: "total_trades",     label: "总交易次数",  fmt: v => String(v),          better: "higher" },
  { key: "win_rate",         label: "胜率",        fmt: v => `${v}%`,            better: "higher" },
  { key: "avg_win_pct",      label: "平均盈利",    fmt: v => `+${v}%`,           better: "higher" },
  { key: "avg_loss_pct",     label: "平均亏损",    fmt: v => `${v}%`,            better: "higher" },
  { key: "profit_factor",    label: "盈亏比",      fmt: v => `${v}x`,            better: "higher", primary: true },
  { key: "total_return_pct", label: "策略总收益",  fmt: v => `${v >= 0 ? "+" : ""}${v}%`, better: "higher" },
  { key: "alpha_pct",        label: "超额收益",    fmt: v => `${v >= 0 ? "+" : ""}${v}%`, better: "higher", primary: true },
  { key: "max_drawdown_pct", label: "最大回撤",    fmt: v => `-${v}%`,           better: "lower",  primary: true },
  { key: "sharpe_ratio",     label: "夏普比率",    fmt: v => String(v),          better: "higher" },
];

function CompareTable({ prev, curr }: { prev: VersionStats; curr: VersionStats }) {
  const ps = prev.stats;
  const cs = curr.stats;

  return (
    <div className="version-compare-table">
      {/* Header */}
      <div className="version-compare-row version-compare-header">
        <div className="vcol-label" />
        <div className="vcol-prev">{prev.label}</div>
        <div className="vcol-curr">{curr.label}</div>
      </div>

      {METRICS.map(({ key, label, fmt, better, primary }) => {
        const pv = ps[key] as number | undefined;
        const cv = cs[key] as number | undefined;
        const pWins = pv !== undefined && cv !== undefined
          ? (better === "higher" ? pv > cv : pv < cv) : false;
        const cWins = pv !== undefined && cv !== undefined
          ? (better === "higher" ? cv > pv : cv < pv) : false;

        return (
          <div key={key} className={`version-compare-row ${primary ? "vcol-primary-row" : ""}`}>
            <div className="vcol-label">
              {primary && <span className="vcol-primary-dot" />}
              {label}
            </div>
            <div className={`vcol-prev ${pWins ? "vcol-win" : ""}`}>
              {pv !== undefined ? fmt(pv) : "—"}
            </div>
            <div className={`vcol-curr ${cWins ? "vcol-win" : ""}`}>
              {cv !== undefined ? fmt(cv) : "—"}
            </div>
          </div>
        );
      })}

      {/* Exit breakdown */}
      <div className="version-compare-row version-compare-exits">
        <div className="vcol-label">离场原因</div>
        <div className="vcol-prev"><ExitBreakdown bd={ps.exit_breakdown} /></div>
        <div className="vcol-curr"><ExitBreakdown bd={cs.exit_breakdown} /></div>
      </div>
    </div>
  );
}

function ExitBreakdown({ bd }: { bd?: Record<string, number> }) {
  if (!bd) return null;
  return (
    <div style={{ display: "flex", flexWrap: "wrap", gap: 4 }}>
      {Object.entries(bd).map(([r, n]) => (
        <span key={r} className="exit-tag">{r.replace("_", " ")} ({n})</span>
      ))}
    </div>
  );
}

// ── Mini equity curve ─────────────────────────────────────────────────────────

function MiniCurve({ label, curve }: { label: string; curve?: number[] }) {
  if (!curve || curve.length < 2) return null;
  const min = Math.min(...curve), max = Math.max(...curve);
  const range = max - min || 1;
  const w = 400, h = 80, pad = 6;
  const pts = curve.map((v, i) => {
    const x = pad + (i / (curve.length - 1)) * (w - 2 * pad);
    const y = h - pad - ((v - min) / range) * (h - 2 * pad);
    return `${x},${y}`;
  }).join(" ");
  const fin = curve[curve.length - 1] - 100;
  const color = fin >= 0 ? "#22c55e" : "#ef4444";
  const baseY = h - pad - ((100 - min) / range) * (h - 2 * pad);

  return (
    <div className="mini-curve-card">
      <div className="mini-curve-title">{label}</div>
      <svg viewBox={`0 0 ${w} ${h}`} className="equity-svg" style={{ width: "100%" }} preserveAspectRatio="none">
        <line x1={pad} y1={baseY} x2={w - pad} y2={baseY} stroke="#2a2d3a" strokeWidth="1" strokeDasharray="3,3" />
        <polygon points={`${pad},${baseY} ${pts} ${w - pad},${baseY}`} fill={color} opacity="0.1" />
        <polyline points={pts} fill="none" stroke={color} strokeWidth="1.5" />
      </svg>
      <div className="mini-curve-ret" style={{ color }}>{fin >= 0 ? "+" : ""}{fin.toFixed(1)}%</div>
    </div>
  );
}
