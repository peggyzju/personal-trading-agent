import { useState, useEffect } from "react";
import { api } from "../api/client";
import type { V8BacktestResult, V8BacktestSide } from "../api/client";

interface Props { backendOnline: boolean }
type Period = "6mo" | "1y" | "2025" | "2024" | "2023";
const PERIODS: { v: Period; label: string }[] = [
  { v: "6mo", label: "近6月" }, { v: "1y", label: "近1年" },
  { v: "2025", label: "2025" }, { v: "2024", label: "2024" }, { v: "2023", label: "2023" },
];

export function BacktestView({ backendOnline }: Props) {
  const [data, setData] = useState<V8BacktestResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [period, setPeriod] = useState<Period>("6mo");

  useEffect(() => {
    if (!backendOnline) return;
    api.getV8Backtest().then(r => { setData(r); if (r.status === "running") pollUntilDone(); }).catch(() => {});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [backendOnline]);

  function pollUntilDone() {
    setLoading(true);
    const poll = setInterval(async () => {
      const r = await api.getV8Backtest();
      setData(r);
      if (r.status !== "running") { clearInterval(poll); setLoading(false); }
    }, 4000);
    setTimeout(() => { clearInterval(poll); setLoading(false); }, 300_000);
  }

  async function run() {
    setLoading(true);
    try { await api.triggerV8Backtest(period); setData({ status: "running", period }); pollUntilDone(); }
    catch { setLoading(false); }
  }

  if (!backendOnline) return null;

  const fmt = (v: number) => `${v >= 0 ? "+" : ""}${v.toFixed(1)}%`;
  type Row = { key: string; label: string; side: V8BacktestSide; strat: boolean };
  const rows: Row[] = [];
  if (data?.status === "done" && data.v8) {
    rows.push({ key: "v8", label: "v8 趋势", side: data.v8, strat: true });
    if (data.qqq) rows.push({ key: "qqq", label: "QQQ 纳指", side: data.qqq, strat: false });
    if (data.spy) rows.push({ key: "spy", label: "SPY 标普", side: data.spy, strat: false });
  }
  const years = data?.v8?.by_year ? Object.keys(data.v8.by_year).sort() : [];

  return (
    <div className="pm-backtest-section">
      <div className="sr-header" style={{ marginTop: 32 }}>
        <div>
          <h2 className="sr-title">📊 v8 回测 — 趋势打法 vs 大盘</h2>
          <p className="sr-subtitle">机械动量(无 AI、无后见之明)· Alpaca 数据 · v8 / QQQ / SPY 同池对照</p>
        </div>
      </div>

      <div className="pm-controls">
        <div className="pm-period-group">
          {PERIODS.map(p => (
            <button key={p.v} className={`pm-period-btn${period === p.v ? " active" : ""}`} onClick={() => setPeriod(p.v)}>
              {p.label}
            </button>
          ))}
        </div>
        <button className="brief-generate-btn" onClick={run} disabled={loading}>
          {loading ? "回测中…" : "▶ 运行回测"}
        </button>
        {data?.status === "done" && (
          <span className="pm-gen-time">{data.date_range} · {data.n_months} 个月</span>
        )}
      </div>

      {data?.status === "running" && <div className="brief-empty">回测中…(取数+模拟约 1-3 分钟)</div>}
      {data?.status === "error" && <div className="pm-error">⚠ 回测失败:{data.error}</div>}

      {data?.status === "done" && rows.length > 0 && (
        <>
          <div className="pm-stats-row">
            {rows.map(r => (
              <div className="pm-stat-card" key={r.key} style={r.strat ? { borderColor: "#22c55e" } : undefined}>
                <div className="pm-stat-label">{r.strat ? "★ " : ""}{r.label} · 总收益</div>
                <div className={`pm-stat-value ${r.side.total_return_pct >= 0 ? "pos" : "neg"}`}>{fmt(r.side.total_return_pct)}</div>
                <div className="pm-stat-label">最大回撤 {r.side.max_drawdown_pct.toFixed(1)}%</div>
              </div>
            ))}
          </div>

          {years.length > 0 && (
            <div className="pm-tier-card">
              <div className="pm-tier-title">分年收益</div>
              <div className="pm-tier-row pm-tier-header">
                <span>策略</span>
                {years.map(y => <span key={y} style={{ textAlign: "right" }}>{y}</span>)}
              </div>
              {rows.map(r => (
                <div className="pm-tier-row" key={r.key}>
                  <span style={{ fontWeight: r.strat ? 700 : 400 }}>{r.key === "v8" ? "v8" : r.label.slice(0, 3)}</span>
                  {years.map(y => {
                    const v = r.side.by_year[y];
                    return <span key={y} className={v != null ? (v >= 0 ? "pos" : "neg") : ""} style={{ textAlign: "right" }}>{v != null ? fmt(v) : "—"}</span>;
                  })}
                </div>
              ))}
            </div>
          )}

          <p className="sr-subtitle" style={{ marginTop: 12 }}>
            ⚠️ 用今天的 S&P500 名单有幸存者偏差(绝对数字偏高);但三者同池对照,相对胜负可信。稳健性见 scripts/v8_robustness.py(9/9 组赢 SPY)。
          </p>
        </>
      )}
    </div>
  );
}
