import { Analysis } from "../api/client";

export function AnalysisPanel({ analysis }: { analysis: Analysis }) {
  const signalColor = analysis.signal === "BUY" ? "#22c55e" : analysis.signal === "SELL" ? "#ef4444" : "#f59e0b";

  return (
    <div className="analysis-panel">
      <div className="analysis-grid">
        <AnalysisStat label="Signal" value={analysis.signal} color={signalColor} />
        <AnalysisStat label="Target Price" value={`$${analysis.target_price?.toFixed(2) ?? "—"}`} />
        <AnalysisStat label="Stop Loss" value={`$${analysis.stop_loss?.toFixed(2) ?? "—"}`} />
        <AnalysisStat label="Confidence" value={`${((analysis.confidence ?? 0) * 100).toFixed(0)}%`} />
      </div>

      <div className="reasoning-section">
        <h4>AI Reasoning</h4>
        <p>{analysis.reasoning}</p>
      </div>

      {analysis.key_risks?.length > 0 && (
        <div className="risks-section">
          <h4>Key Risks</h4>
          <ul>
            {analysis.key_risks.map((r, i) => <li key={i}>{r}</li>)}
          </ul>
        </div>
      )}
    </div>
  );
}

function AnalysisStat({ label, value, color }: { label: string; value: string; color?: string }) {
  return (
    <div className="analysis-stat">
      <span className="analysis-stat-label">{label}</span>
      <span className="analysis-stat-value" style={color ? { color } : undefined}>{value}</span>
    </div>
  );
}
