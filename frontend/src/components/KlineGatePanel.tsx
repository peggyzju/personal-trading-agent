import { useEffect, useState } from "react";

interface Gate { key: string; label: string; pass: boolean; value: string; }
interface KlineAnalysis {
  symbol: string; name: string; rank: number | null;
  rsi: (number | null)[];
  indicators: Record<string, number>;
  gates: Gate[];
  volume_info: { label: string; value: string; is_high: boolean };
  summary: { passed: number; total: number; v8_eligible: boolean };
  scan_snapshot?: {
    price?: number | null;
    rsi?: number | null;
    vs_ma20?: number | null;
    momentum_3m?: number | null;
    passed: number;
    total: number;
    v8_eligible: boolean;
    gates: Record<string, boolean>;
  } | null;
  ai_comment: string;
  as_of: string;
  error?: string;
}

function num(v: number | null | undefined, suffix = "", digits = 1): string {
  if (v == null || Number.isNaN(v)) return "-";
  return `${Number(v).toFixed(digits)}${suffix}`;
}

// RSI 迷你折线（SVG sparkline，标 50/80 参考线）
function RsiSpark({ rsi }: { rsi: (number | null)[] }) {
  const vals = rsi.filter((v): v is number => v != null).slice(-60);
  if (vals.length < 2) return null;
  const W = 100, H = 28, lo = 30, hi = 90;
  const y = (v: number) => H - ((Math.min(hi, Math.max(lo, v)) - lo) / (hi - lo)) * H;
  const x = (i: number) => (i / (vals.length - 1)) * W;
  const d = vals.map((v, i) => `${i ? "L" : "M"}${x(i).toFixed(1)} ${y(v).toFixed(1)}`).join(" ");
  const last = vals[vals.length - 1];
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
      <span style={{ fontSize: 11, color: "var(--muted)", minWidth: 28 }}>RSI</span>
      <svg viewBox={`0 0 ${W} ${H}`} width="100%" height={H} preserveAspectRatio="none" style={{ flex: 1 }}>
        <line x1="0" y1={y(80)} x2={W} y2={y(80)} stroke="#ef444455" strokeWidth="0.5" strokeDasharray="2 2" />
        <line x1="0" y1={y(50)} x2={W} y2={y(50)} stroke="#8b93a255" strokeWidth="0.5" strokeDasharray="2 2" />
        <path d={d} fill="none" stroke="#a78bfa" strokeWidth="1" vectorEffect="non-scaling-stroke" />
      </svg>
      <span style={{ fontSize: 11, fontWeight: 600, color: last >= 50 && last <= 80 ? "#22c55e" : "#f59e0b", minWidth: 22 }}>
        {Math.round(last)}
      </span>
    </div>
  );
}

/** v8 核心指标解读面板：AI 点评 + RSI 迷你图 + 4 门控 + 量能参考 + 综合。
 *  自取 /api/analyze/kline/{symbol}，放在 K 线图下方复用（信号 / 持仓）。 */
export function KlineGatePanel({ symbol }: { symbol: string }) {
  const [data, setData] = useState<KlineAnalysis | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setData(null); setErr(null);
    (async () => {
      try {
        const res = await fetch(`/api/analyze/kline/${symbol}`);
        const d: KlineAnalysis = await res.json();
        if (cancelled) return;
        if (d.error) { setErr(d.error); return; }
        setData(d);
      } catch { if (!cancelled) setErr("分析加载失败"); }
    })();
    return () => { cancelled = true; };
  }, [symbol]);

  if (err) return <div className="kgp" style={{ color: "var(--muted)", fontSize: 12 }}>指标分析：{err}</div>;
  if (!data) return <div className="kgp" style={{ color: "var(--muted)", fontSize: 12 }}>指标分析加载中…</div>;

  const elig = data.summary.v8_eligible;
  const snap = data.scan_snapshot;
  const curRsi = data.indicators?.rsi;
  const scanRsi = snap?.rsi;
  const changed = snap && snap.v8_eligible !== elig;
  return (
    <div className="kgp">
      {/* AI 一句话点评 */}
      <div style={{
        display: "flex", gap: 8, alignItems: "flex-start", padding: "8px 10px",
        background: "#60a5fa14", border: "1px solid #60a5fa33", borderRadius: 8, marginBottom: 10,
      }}>
        <span style={{ fontSize: 13 }}>🤖</span>
        <span style={{ fontSize: 12, lineHeight: 1.65, color: "#93c5fd", whiteSpace: "pre-line" }}>{data.ai_comment}</span>
      </div>

      {snap && (
        <div style={{
          display: "grid", gridTemplateColumns: "1fr 1fr", gap: 6,
          marginBottom: 10,
        }}>
          <div style={{
            border: "1px solid #262a33", borderRadius: 6, padding: "6px 9px",
            background: snap.v8_eligible ? "#22c55e12" : "#ef444412",
          }}>
            <div style={{ fontSize: 10, color: "var(--muted)", marginBottom: 2 }}>扫描时</div>
            <div style={{ fontSize: 12, fontWeight: 700, color: snap.v8_eligible ? "#22c55e" : "#ef4444" }}>
              RSI {num(scanRsi, "", 0)} · {snap.passed}/{snap.total} 通过
            </div>
          </div>
          <div style={{
            border: `1px solid ${changed ? "#f59e0b66" : "#262a33"}`, borderRadius: 6, padding: "6px 9px",
            background: changed ? "#f59e0b12" : (elig ? "#22c55e12" : "#ef444412"),
          }}>
            <div style={{ fontSize: 10, color: "var(--muted)", marginBottom: 2 }}>当前</div>
            <div style={{ fontSize: 12, fontWeight: 700, color: elig ? "#22c55e" : "#ef4444" }}>
              RSI {num(curRsi, "", 0)} · {data.summary.passed}/{data.summary.total} 通过
            </div>
          </div>
        </div>
      )}

      {/* RSI 迷你图 */}
      <div style={{ marginBottom: 10 }}><RsiSpark rsi={data.rsi} /></div>

      {/* 4 门控 */}
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 6 }}>
        {data.gates.map(g => (
          <div key={g.key} style={{
            display: "flex", alignItems: "center", gap: 6,
            background: "var(--card-2, #161a22)", border: "1px solid #262a33",
            borderRadius: 6, padding: "6px 9px",
          }}>
            <span style={{ color: g.pass ? "#22c55e" : "#ef4444", fontWeight: 700, fontSize: 13 }}>
              {g.pass ? "✓" : "✗"}
            </span>
            <span style={{ fontSize: 12, fontWeight: 600, minWidth: 28 }}>{g.label}</span>
            <span style={{ fontSize: 11, color: "var(--muted)", lineHeight: 1.3 }}>{g.value}</span>
          </div>
        ))}
        {/* 量能：参考行（非门控）*/}
        <div style={{
          display: "flex", alignItems: "center", gap: 6,
          background: "var(--card-2, #161a22)", border: "1px dashed #262a33",
          borderRadius: 6, padding: "6px 9px",
        }}>
          <span style={{ color: data.volume_info.is_high ? "#22c55e" : "#8b93a2", fontSize: 13 }}>●</span>
          <span style={{ fontSize: 12, fontWeight: 600, minWidth: 28 }}>{data.volume_info.label}</span>
          <span style={{ fontSize: 11, color: "var(--muted)" }}>{data.volume_info.value} <em style={{ opacity: 0.6, fontStyle: "normal" }}>· 参考</em></span>
        </div>
        {/* 综合 */}
        <div style={{
          display: "flex", alignItems: "center", gap: 6,
          background: elig ? "#22c55e18" : "#ef444412",
          border: `1px solid ${elig ? "#22c55e44" : "#ef444433"}`,
          borderRadius: 6, padding: "6px 9px",
        }}>
          <span style={{ color: elig ? "#22c55e" : "#ef4444", fontSize: 14 }}>{elig ? "✓" : "✕"}</span>
          <span style={{ fontSize: 12, fontWeight: 600, color: elig ? "#22c55e" : "#ef4444" }}>
            {data.summary.passed} / {data.summary.total} 通过 → {elig ? "符合 v8 买入" : "不符合"}
          </span>
        </div>
      </div>
    </div>
  );
}
