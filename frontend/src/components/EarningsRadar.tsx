import { useState, useEffect } from "react";
import { api, type EarningsCalendar, type EarningsAnalysisItem } from "../api/client";

// 财报雷达:① 已出财报 → AI 研判(跳空反应 + 入场/持仓建议);② 未来7天财报日历(持仓优先)。人工决策,不下单。

function verdictColor(v: string): string {
  if (/持有|值得关注|加仓/.test(v)) return "var(--green)";
  if (/清仓|回避|减仓/.test(v)) return "var(--red)";
  return "#d4a017";
}

function AnalysisCard({ a }: { a: EarningsAnalysisItem }) {
  const gap = a.gap_pct ?? 0;
  const gapColor = gap >= 0 ? "var(--green)" : "var(--red)";
  const an = a.analysis || { summary: "", verdict: "", confidence: 0, reason: "" };
  const hist = a.history?.filter(h => h.reaction_pct != null) || [];
  return (
    <div style={{ border: "1px solid var(--border)", borderRadius: 8, padding: "9px 11px", marginBottom: 7, background: "rgba(255,255,255,0.02)" }}>
      {/* 第一行:代码 + 建议 + 财报后跳空(明确标注) */}
      <div style={{ display: "flex", alignItems: "center", gap: 7, marginBottom: 6 }}>
        <span style={{ fontFamily: "monospace", fontSize: 14, fontWeight: 500 }}>{a.symbol}</span>
        {a.held && <span style={{ fontSize: 9, color: "var(--red)", border: "1px solid var(--red)", borderRadius: 4, padding: "0 4px" }}>持仓</span>}
        <span style={{ fontSize: 12, fontWeight: 500, color: verdictColor(an.verdict) }}>
          {a.held ? "持仓建议" : "入场建议"}：{an.verdict}
        </span>
        <span style={{ marginLeft: "auto", display: "flex", alignItems: "baseline", gap: 5 }}>
          <span style={{ fontSize: 9, color: "var(--muted)" }}>财报后跳空</span>
          <span style={{ fontSize: 17, fontWeight: 600, color: gapColor }}>{gap >= 0 ? "+" : ""}{gap}%</span>
        </span>
      </div>

      {/* 第二行:本次财报关键指标(都带标注) */}
      <div style={{ display: "flex", flexWrap: "wrap", gap: "3px 14px", fontSize: 11, color: "var(--muted)", marginBottom: hist.length ? 7 : 4 }}>
        {a.surprise_pct != null && (
          <span>EPS超预期 <b style={{ color: a.surprise_pct >= 0 ? "var(--green)" : "var(--red)", fontWeight: 500 }}>{a.surprise_pct >= 0 ? "+" : ""}{a.surprise_pct}%</b></span>
        )}
        {a.vol_ratio != null && <span title="本次成交量 / 20日均量">量比 <b style={{ color: "var(--text)", fontWeight: 500 }}>{a.vol_ratio}×</b></span>}
        <span title="AI 对该建议的把握程度">AI信心 <b style={{ color: "var(--text)", fontWeight: 500 }}>{an.confidence}/10</b></span>
      </div>

      {/* 历次财报反应:对齐小表(季度 · 超预期 → 当日股价)*/}
      {hist.length > 0 && (
        <div style={{ marginBottom: 7 }}>
          <div style={{ fontSize: 9, color: "var(--muted)", marginBottom: 3 }}>历次财报反应 · 超预期 → 当日股价</div>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(118px, 1fr))", gap: "2px 12px", fontSize: 10 }}>
            {hist.map(h => (
              <div key={h.date} style={{ display: "flex", gap: 5, fontFamily: "monospace" }}>
                <span style={{ color: "var(--muted)", minWidth: 34 }}>{h.date.slice(2, 7)}</span>
                {h.surprise_pct != null && (
                  <span style={{ color: h.surprise_pct >= 0 ? "var(--green)" : "var(--red)", minWidth: 38, textAlign: "right" }}>
                    {h.surprise_pct >= 0 ? "+" : ""}{Math.round(h.surprise_pct)}%
                  </span>
                )}
                <span style={{ color: "var(--muted)" }}>→</span>
                <span style={{ color: (h.reaction_pct ?? 0) >= 0 ? "var(--green)" : "var(--red)", minWidth: 38, textAlign: "right" }}>
                  {(h.reaction_pct ?? 0) >= 0 ? "+" : ""}{Math.round(h.reaction_pct ?? 0)}%
                </span>
              </div>
            ))}
          </div>
        </div>
      )}

      {an.summary && <p style={{ fontSize: 11, lineHeight: 1.55, margin: 0, color: "var(--text)" }}>{an.summary}</p>}
      {an.reason && <p style={{ fontSize: 10, color: "var(--muted)", margin: "3px 0 0", lineHeight: 1.45 }}>{an.reason}</p>}
    </div>
  );
}

function dayLabel(d: number): string {
  if (d <= 0) return "今天";
  if (d === 1) return "明天";
  return `${d}天后`;
}

export default function EarningsRadar() {
  const [cal, setCal] = useState<EarningsCalendar | null>(null);
  const [analysis, setAnalysis] = useState<EarningsAnalysisItem[]>([]);

  useEffect(() => {
    const load = () => {
      api.getEarningsCalendar().then(setCal).catch(() => {});
      api.getEarningsAnalysis().then(r => setAnalysis(r.items || [])).catch(() => {});
    };
    load();
    const t = setInterval(load, 60000);
    return () => clearInterval(t);
  }, []);

  const rows = cal?.rows || [];
  const holdingsReporting = cal?.holdings_reporting || 0;

  return (
    <div style={{ border: "1px solid var(--border)", borderRadius: 10, padding: "9px 12px" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 9 }}>
        <span style={{ fontSize: 12, fontWeight: 500 }}>📅 财报雷达</span>
        <span style={{ fontSize: 10, color: "var(--muted)" }}>持仓优先 · 人工决策</span>
        {holdingsReporting > 0 && (
          <span style={{ fontSize: 10, color: "var(--red)", background: "rgba(239,68,68,0.12)", borderRadius: 4, padding: "1px 6px" }}>
            ⚠️ {holdingsReporting} 只持仓即将发财报
          </span>
        )}
        <span style={{ marginLeft: "auto", fontSize: 10, color: "var(--muted)" }}>未来7天 {cal?.count ?? 0} 只</span>
      </div>

      {/* 区块 1:已出财报 → AI 研判 */}
      {analysis.length > 0 && (
        <div style={{ marginBottom: 11 }}>
          <div style={{ fontSize: 10, color: "var(--muted)", fontWeight: 500, marginBottom: 6, textTransform: "none" }}>
            📊 已出财报 · AI 研判
          </div>
          {analysis.slice(0, 4).map(a => <AnalysisCard key={a.symbol} a={a} />)}
        </div>
      )}

      {/* 区块 2:未来财报 · 日历 */}
      <div>
        <div style={{ fontSize: 10, color: "var(--muted)", fontWeight: 500, marginBottom: 6 }}>
          🗓 未来财报 · 日历
        </div>
        {rows.length === 0 ? (
          <div style={{ fontSize: 11, color: "var(--muted)" }}>未来7天暂无财报(淡季)。每天 8:00 刷新。</div>
        ) : (
          <div style={{ display: "flex", flexWrap: "wrap", gap: "5px 8px" }}>
            {rows.slice(0, 16).map(r => {
              const soon = r.in_portfolio && r.days_until <= 1;
              return (
                <span key={r.symbol} title={`${r.date} · ${r.session === "BMO" ? "盘前公布" : r.session === "AMC" ? "盘后公布" : "时段未知"} · ${r.importance}`} style={{
                  display: "inline-flex", alignItems: "center", gap: 5, fontSize: 11,
                  padding: "3px 8px", borderRadius: 6,
                  border: `1px solid ${r.in_portfolio ? "var(--red)" : "var(--border)"}`,
                  ...(soon ? { background: "rgba(239,68,68,0.14)" } : r.in_portfolio ? { background: "rgba(239,68,68,0.06)" } : {}),
                }}>
                  <span style={{ fontFamily: "monospace", fontWeight: 500, color: r.in_portfolio ? "var(--red)" : "var(--text)" }}>{r.symbol}</span>
                  {r.in_portfolio && <span style={{ fontSize: 8, color: "var(--red)" }}>持仓</span>}
                  <span style={{ color: "var(--text)" }}>{r.date.slice(5)}</span>
                  {r.session !== "?" && <span style={{ color: "var(--muted)", fontSize: 10 }}>{r.session === "BMO" ? "盘前" : "盘后"}</span>}
                  <span style={{ color: soon ? "var(--red)" : "var(--muted)", fontSize: 10, fontWeight: soon ? 500 : 400 }}>{dayLabel(r.days_until)}</span>
                </span>
              );
            })}
          </div>
        )}
        <div style={{ fontSize: 9, color: "var(--muted)", marginTop: 6 }}>盘前=开盘前公布 · 盘后=收盘后公布 · 红框=持仓股(财报前会被买入门挡住)</div>
      </div>
    </div>
  );
}
