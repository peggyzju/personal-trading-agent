import { useState, useEffect } from "react";
import { api } from "../api/client";
import type { V8BacktestResult } from "../api/client";

interface Props { backendOnline: boolean }

type Period = "6mo" | "1y" | "2025" | "2024" | "2023";

export function BacktestView({ backendOnline }: Props) {
  const [data, setData] = useState<V8BacktestResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [period, setPeriod] = useState<Period>("6mo");

  useEffect(() => {
    if (!backendOnline) return;
    api.getV8Backtest().then(r => {
      setData(r);
      if (r.status === "running") pollUntilDone();
    }).catch(() => {});
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
    try {
      await api.triggerV8Backtest(period);
      setData({ status: "running", period });
      pollUntilDone();
    } catch { setLoading(false); }
  }

  if (!backendOnline) return <div className="brief-offline">启动后端服务以运行回测。</div>;

  const fmt = (v: number) => `${v >= 0 ? "+" : ""}${v.toFixed(1)}%`;
  const col = (v: number) => (v >= 0 ? "#22c55e" : "#ef4444");
  const years = data?.v8?.by_year ? Object.keys(data.v8.by_year).sort() : [];

  return (
    <div className="backtest-container">
      <div className="scan-header">
        <div>
          <h2>📊 v8 回测 — 趋势打法 vs SPY</h2>
          <span className="scan-meta">
            机械动量(无 AI、无后见之明)· Alpaca 数据 · 同股票池对照 SPY
          </span>
        </div>
        <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
          <select value={period} onChange={e => setPeriod(e.target.value as Period)} className="config-input">
            <option value="6mo">近 6 个月</option>
            <option value="1y">近 1 年</option>
            <option value="2025">2025 全年</option>
            <option value="2024">2024 全年</option>
            <option value="2023">2023 全年</option>
          </select>
          <button className="scan-btn" onClick={run} disabled={loading}>
            {loading ? "回测中…" : "▶ 运行 v8 回测"}
          </button>
        </div>
      </div>

      {(!data || data.status === "not_run") && (
        <div className="brief-empty">选时间段,点「运行 v8 回测」。</div>
      )}
      {data?.status === "running" && <div className="brief-empty">回测中…(取数+模拟约 1-3 分钟)</div>}
      {data?.status === "error" && <div className="brief-empty">回测失败:{data.error}</div>}

      {data?.status === "done" && data.v8 && data.spy && (
        <>
          <div style={{ fontSize: 12, color: "var(--muted)", margin: "4px 0 12px" }}>
            {data.date_range} · {data.n_months} 个月
          </div>
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12, marginBottom: 14 }}>
            {([["v8 趋势打法", data.v8, "#22c55e"], ["SPY 买入持有", data.spy, "#64748b"]] as const).map(([label, side, accent]) => (
              <div key={label} style={{ border: `1px solid var(--border)`, borderLeft: `3px solid ${accent}`, borderRadius: 12, padding: "12px 14px" }}>
                <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 8 }}>{label}</div>
                <div style={{ display: "flex", gap: 18 }}>
                  <div>
                    <div style={{ fontSize: 11, color: "var(--muted)" }}>总收益</div>
                    <div style={{ fontSize: 22, fontWeight: 700, color: col(side.total_return_pct) }}>{fmt(side.total_return_pct)}</div>
                  </div>
                  <div>
                    <div style={{ fontSize: 11, color: "var(--muted)" }}>最大回撤</div>
                    <div style={{ fontSize: 22, fontWeight: 700, color: "#ef4444" }}>{side.max_drawdown_pct.toFixed(1)}%</div>
                  </div>
                </div>
              </div>
            ))}
          </div>

          {years.length > 0 && (
            <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
              <thead>
                <tr style={{ color: "var(--muted)", fontSize: 11, textAlign: "right" }}>
                  <th style={{ textAlign: "left", padding: "6px 4px" }}>分年收益</th>
                  {years.map(y => <th key={y} style={{ padding: "6px 4px" }}>{y}</th>)}
                </tr>
              </thead>
              <tbody>
                {([["v8", data.v8], ["SPY", data.spy]] as const).map(([label, side]) => (
                  <tr key={label} style={{ borderTop: "1px solid var(--border)" }}>
                    <td style={{ padding: "6px 4px", fontWeight: 500 }}>{label}</td>
                    {years.map(y => {
                      const v = side.by_year[y];
                      return <td key={y} style={{ padding: "6px 4px", textAlign: "right", color: v != null ? col(v) : "var(--muted)" }}>{v != null ? fmt(v) : "—"}</td>;
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
          )}

          <div style={{ fontSize: 11, color: "var(--muted)", marginTop: 12, lineHeight: 1.5 }}>
            ⚠️ 用今天的 S&P500 名单有幸存者偏差(绝对数字偏高);但 v8 与 SPY 同池对照,<b>相对胜负可信</b>。
            稳健性见 <code>scripts/v8_robustness.py</code>(9/9 组参数赢 SPY)。
          </div>
        </>
      )}
    </div>
  );
}
