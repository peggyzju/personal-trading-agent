import { useState, useEffect } from "react";
import { api, type EarningsCalendar, type EarningsAnalysisItem } from "../api/client";

// 财报雷达:① 财报后 AI 研判卡片(实时);② 未来7天财报日历(持仓优先)。
// 人工决策,系统不自动下单。设计见 docs/EARNINGS_RADAR_PLAN.md。

function verdictColor(v: string): string {
  if (/持有|值得关注/.test(v)) return "var(--green)";
  if (/清仓/.test(v)) return "var(--red)";
  return "#d4a017"; // 减仓 / 观望
}

function AnalysisCard({ a }: { a: EarningsAnalysisItem }) {
  const gap = a.gap_pct ?? 0;
  const gapColor = gap >= 0 ? "var(--green)" : "var(--red)";
  const an = a.analysis || { summary: "", verdict: "", confidence: 0, reason: "" };
  return (
    <div style={{ border: "1px solid var(--border)", borderRadius: 12, padding: "12px 14px", marginBottom: 10, background: "rgba(255,255,255,0.02)" }}>
      <div style={{ display: "flex", alignItems: "baseline", gap: 8, marginBottom: 8 }}>
        <span style={{ fontFamily: "monospace", fontSize: 17, fontWeight: 500 }}>{a.symbol}</span>
        {a.held && <span style={{ fontSize: 10, color: "var(--red)", border: "1px solid var(--red)", borderRadius: 6, padding: "1px 6px" }}>持仓</span>}
        <span style={{ fontSize: 11, color: "var(--muted)" }}>财报后</span>
        <span style={{ marginLeft: "auto", fontSize: 19, fontWeight: 500, color: gapColor }}>{gap >= 0 ? "+" : ""}{gap}%</span>
      </div>
      <div style={{ display: "flex", gap: 16, fontSize: 12, color: "var(--muted)", marginBottom: 8 }}>
        {a.surprise_pct != null && <span>EPS超预期 <b style={{ color: "var(--text)" }}>{a.surprise_pct}%</b></span>}
        {a.vol_ratio != null && <span>量比 <b style={{ color: "var(--text)" }}>{a.vol_ratio}x</b></span>}
      </div>
      {an.summary && <p style={{ fontSize: 13, lineHeight: 1.6, margin: "0 0 8px" }}>{an.summary}</p>}
      <div style={{ display: "flex", alignItems: "center", gap: 8, background: "rgba(255,255,255,0.03)", borderRadius: 8, padding: "8px 10px" }}>
        <span style={{ fontSize: 13, fontWeight: 500, color: verdictColor(an.verdict) }}>{a.held ? "持仓建议" : "入场研判"}：{an.verdict}</span>
        <span style={{ marginLeft: "auto", fontSize: 11, color: "var(--muted)" }}>信心 {an.confidence}/10</span>
      </div>
      {an.reason && <p style={{ fontSize: 11, color: "var(--muted)", margin: "6px 0 0", lineHeight: 1.5 }}>{an.reason}</p>}
      {a.history?.length > 0 && (
        <div style={{ marginTop: 8, fontSize: 11, color: "var(--muted)" }}>
          历史财报后：{a.history.filter(h => h.reaction_pct != null).map(h => (
            <span key={h.date} style={{ marginRight: 8, color: (h.reaction_pct ?? 0) >= 0 ? "var(--green)" : "var(--red)" }}>
              {(h.reaction_pct ?? 0) >= 0 ? "+" : ""}{h.reaction_pct}%
            </span>
          ))}
        </div>
      )}
      <div style={{ marginTop: 8, fontSize: 10, color: "var(--muted)", textAlign: "right" }}>人工决策 · 系统不自动下单</div>
    </div>
  );
}

function dayLabel(daysUntil: number): string {
  if (daysUntil <= 0) return "今天";
  if (daysUntil === 1) return "明天";
  return `${daysUntil}天后`;
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
    <div style={{ border: "1px solid var(--border)", borderRadius: 12, padding: "12px 14px" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 10 }}>
        <span style={{ fontSize: 14, fontWeight: 500 }}>📅 财报雷达</span>
        <span style={{ fontSize: 11, color: "var(--muted)" }}>未来 7 天 · 全市场 · 持仓优先</span>
        {cal?.generated_at && <span style={{ marginLeft: "auto", fontSize: 10, color: "var(--muted)" }}>{cal.count} 只</span>}
      </div>

      {analysis.length > 0 && (
        <div style={{ marginBottom: 12 }}>
          {analysis.slice(0, 4).map(a => <AnalysisCard key={a.symbol} a={a} />)}
        </div>
      )}

      {holdingsReporting > 0 && (
        <div style={{ display: "flex", alignItems: "center", gap: 8, background: "rgba(239,68,68,0.12)", color: "var(--red)", borderRadius: 8, padding: "7px 10px", fontSize: 12, marginBottom: 10 }}>
          ⚠️ {holdingsReporting} 只持仓本周发财报 — 注意裸穿财报跳空风险
        </div>
      )}

      {rows.length === 0 ? (
        <div style={{ fontSize: 12, color: "var(--muted)", padding: "8px 0" }}>未来 7 天暂无财报(财报淡季)。每天 8:00 自动刷新。</div>
      ) : (
        <div>
          {rows.slice(0, 12).map(r => (
            <div key={r.symbol} style={{
              display: "flex", alignItems: "center", gap: 10, padding: "8px 6px",
              borderBottom: "1px solid var(--border)",
              ...(r.in_portfolio ? { background: "rgba(239,68,68,0.07)", borderLeft: "2px solid var(--red)" } : {}),
            }}>
              <span style={{ width: 56, fontSize: 11, color: r.in_portfolio ? "var(--red)" : "var(--muted)" }}>{dayLabel(r.days_until)}</span>
              <span style={{ flex: 1, minWidth: 0, fontFamily: "monospace", fontWeight: 500 }}>{r.symbol}</span>
              <span style={{ fontSize: 11, color: "var(--muted)" }}>{r.date.slice(5)}</span>
              {r.importance && (
                <span style={{
                  fontSize: 10, borderRadius: 6, padding: "1px 7px",
                  color: r.in_portfolio ? "var(--red)" : "var(--muted)",
                  border: `1px solid ${r.in_portfolio ? "var(--red)" : "var(--border)"}`,
                }}>{r.importance}</span>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
