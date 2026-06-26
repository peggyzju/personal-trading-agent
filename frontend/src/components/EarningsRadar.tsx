import { useState, useEffect } from "react";
import { api, type EarningsCalendar, type EarningsAnalysisItem } from "../api/client";

// 财报雷达(紧凑版):① 财报后 AI 研判;② 未来7天财报日历(持仓优先)。人工决策,不下单。

function verdictColor(v: string): string {
  if (/持有|值得关注/.test(v)) return "var(--green)";
  if (/清仓/.test(v)) return "var(--red)";
  return "#d4a017";
}

function AnalysisCard({ a }: { a: EarningsAnalysisItem }) {
  const gap = a.gap_pct ?? 0;
  const gapColor = gap >= 0 ? "var(--green)" : "var(--red)";
  const an = a.analysis || { summary: "", verdict: "", confidence: 0, reason: "" };
  return (
    <div style={{ border: "1px solid var(--border)", borderRadius: 8, padding: "8px 10px", marginBottom: 6, background: "rgba(255,255,255,0.02)" }}>
      <div style={{ display: "flex", alignItems: "baseline", gap: 6, marginBottom: 4 }}>
        <span style={{ fontFamily: "monospace", fontSize: 13, fontWeight: 500 }}>{a.symbol}</span>
        {a.held && <span style={{ fontSize: 9, color: "var(--red)", border: "1px solid var(--red)", borderRadius: 4, padding: "0 4px" }}>持仓</span>}
        <span style={{ fontSize: 12, fontWeight: 500, color: verdictColor(an.verdict) }}>
          {a.held ? "持仓" : "入场"}：{an.verdict}
        </span>
        <span style={{ fontSize: 10, color: "var(--muted)" }}>信心{an.confidence}</span>
        <span style={{ marginLeft: "auto", fontSize: 14, fontWeight: 500, color: gapColor }}>{gap >= 0 ? "+" : ""}{gap}%</span>
      </div>
      <div style={{ fontSize: 11, color: "var(--muted)", display: "flex", gap: 10, marginBottom: 3 }}>
        {a.surprise_pct != null && <span>本次EPS {a.surprise_pct >= 0 ? "超" : "差"}{Math.abs(a.surprise_pct)}%</span>}
        {a.vol_ratio != null && <span>量比 {a.vol_ratio}x</span>}
      </div>
      {a.history?.filter(h => h.reaction_pct != null).length > 0 && (
        <div style={{ fontSize: 10, color: "var(--muted)", marginBottom: 3 }}>
          历次 财报→股价：
          {a.history.filter(h => h.reaction_pct != null).map(h => (
            <span key={h.date} title={h.date} style={{ marginRight: 8 }}>
              {h.surprise_pct != null && (
                <span style={{ color: h.surprise_pct >= 0 ? "var(--green)" : "var(--red)" }}>
                  {h.surprise_pct >= 0 ? "超" : "差"}{Math.abs(Math.round(h.surprise_pct))}%
                </span>
              )}
              <span style={{ color: "var(--muted)" }}>→</span>
              <span style={{ color: (h.reaction_pct ?? 0) >= 0 ? "var(--green)" : "var(--red)" }}>
                {(h.reaction_pct ?? 0) >= 0 ? "+" : ""}{Math.round(h.reaction_pct ?? 0)}%
              </span>
            </span>
          ))}
        </div>
      )}
      {an.summary && <p style={{ fontSize: 11, lineHeight: 1.5, margin: 0, color: "var(--text)" }}>{an.summary}</p>}
      {an.reason && <p style={{ fontSize: 10, color: "var(--muted)", margin: "3px 0 0", lineHeight: 1.45 }}>{an.reason}</p>}
    </div>
  );
}

function dayLabel(d: number): string {
  if (d <= 0) return "今天";
  if (d === 1) return "明天";
  return `${d}天`;
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
    <div style={{ border: "1px solid var(--border)", borderRadius: 10, padding: "8px 11px" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 7 }}>
        <span style={{ fontSize: 12, fontWeight: 500 }}>📅 财报雷达</span>
        <span style={{ fontSize: 10, color: "var(--muted)" }}>未来7天 · 全市场 · 持仓优先</span>
        {holdingsReporting > 0 && (
          <span style={{ fontSize: 10, color: "var(--red)", background: "rgba(239,68,68,0.12)", borderRadius: 4, padding: "1px 6px" }}>
            ⚠️ {holdingsReporting} 只持仓发财报
          </span>
        )}
        <span style={{ marginLeft: "auto", fontSize: 10, color: "var(--muted)" }}>{cal?.count ?? 0} 只</span>
      </div>

      {analysis.length > 0 && (
        <div style={{ marginBottom: 7 }}>
          {analysis.slice(0, 4).map(a => <AnalysisCard key={a.symbol} a={a} />)}
        </div>
      )}

      {rows.length === 0 ? (
        <div style={{ fontSize: 11, color: "var(--muted)" }}>未来7天暂无财报(淡季)。每天 8:00 刷新。</div>
      ) : (
        <div style={{ display: "flex", flexWrap: "wrap", gap: "4px 8px" }}>
          {rows.slice(0, 16).map(r => (
            <span key={r.symbol} title={`${r.date} ${r.importance}`} style={{
              display: "inline-flex", alignItems: "center", gap: 5, fontSize: 11,
              padding: "2px 7px", borderRadius: 6,
              border: `1px solid ${r.in_portfolio ? "var(--red)" : "var(--border)"}`,
              ...(r.in_portfolio ? { background: "rgba(239,68,68,0.07)" } : {}),
            }}>
              <span style={{ fontFamily: "monospace", fontWeight: 500, color: r.in_portfolio ? "var(--red)" : "var(--text)" }}>{r.symbol}</span>
              <span style={{ color: "var(--muted)" }}>{dayLabel(r.days_until)}</span>
            </span>
          ))}
        </div>
      )}
    </div>
  );
}
