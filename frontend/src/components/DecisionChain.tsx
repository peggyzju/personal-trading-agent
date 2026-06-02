/** 决策卡（阶段1）：用现有数据展示 Maya → Scout → Rex 的决策链。
 *  缺失字段优雅省略，不含逐项门控 ✓/✗（那需后端结构化记录，阶段2）。 */
export interface DecisionInput {
  symbol: string;
  signal?: string | null;
  confidence?: number | null;
  screen_track?: string | null;
  ai_score?: number | null;
  rsi?: number | null;
  vs_ma20_pct?: number | null;
  volume_ratio?: number | null;
  reason?: string | null;
  entry_note?: string | null;
  stop_loss?: number | null;
  target_price?: number | null;
  price?: number | null;
}

const TRACK_LABEL: Record<string, string> = {
  momentum: "Track 1 · 动能突破",
  compression: "Track 2 · 盘整蓄力",
  watchlist: "自选股",
};
const REGIME_LABEL: Record<string, string> = {
  BULL: "牛市", BEAR: "熊市", NEUTRAL: "震荡", CAUTION: "谨慎",
};
const SIG_COLOR: Record<string, string> = {
  STRONG_BUY: "#16a34a", BUY: "#22c55e", HOLD: "#64748b", REDUCE: "#f59e0b", SELL: "#ef4444", ADD: "#6366f1",
};

function num(v: number | null | undefined, suffix = "", digits = 1): string {
  return v == null ? "—" : `${v.toFixed(digits)}${suffix}`;
}

export function DecisionChain({ d, regime, aggression }: {
  d: DecisionInput;
  regime?: string | null;
  aggression?: string | null;
}) {
  const sig = d.signal ?? "—";
  const sigColor = SIG_COLOR[sig] ?? "#64748b";
  const track = d.screen_track ? (TRACK_LABEL[d.screen_track] ?? d.screen_track) : null;

  return (
    <div className="dchain">
      {/* 1. Maya — 市场环境 */}
      <div className="dchain-step">
        <div className="dchain-icon" style={{ background: "rgba(99,102,241,.15)", color: "#818cf8" }}>🧠</div>
        <div className="dchain-body">
          <div className="dchain-head">Maya · 市场环境</div>
          <div className="dchain-row">
            {regime
              ? <span className="dchain-badge" style={{ color: "#818cf8", background: "rgba(99,102,241,.13)" }}>
                  {regime} {REGIME_LABEL[regime] ?? ""}
                </span>
              : <span className="dchain-dim">环境未知</span>}
            {aggression && <span className="dchain-dim">激进度 {aggression}</span>}
          </div>
        </div>
      </div>

      {/* 2. Scout — 选股 */}
      <div className="dchain-step">
        <div className="dchain-icon" style={{ background: "rgba(6,182,212,.15)", color: "#22d3ee" }}>🔍</div>
        <div className="dchain-body">
          <div className="dchain-head">Scout · 选股</div>
          <div className="dchain-row">
            {track && <span className="dchain-badge" style={{ color: "#22d3ee", background: "rgba(6,182,212,.13)" }}>{track}</span>}
            {d.ai_score != null && <span className="dchain-badge" style={{ color: "#e6e9ef", background: "rgba(255,255,255,.07)" }}>AI {d.ai_score}/10</span>}
          </div>
          <div className="dchain-metrics">
            <span>RSI <b>{num(d.rsi, "", 0)}</b></span>
            <span>vs MA20 <b>{num(d.vs_ma20_pct, "%")}</b></span>
            <span>量比 <b>{num(d.volume_ratio, "x", 2)}</b></span>
          </div>
          {(d.entry_note || d.reason) && (
            <div className="dchain-reason">{d.entry_note || d.reason}</div>
          )}
        </div>
      </div>

      {/* 3. Rex — 执行 */}
      <div className="dchain-step">
        <div className="dchain-icon" style={{ background: "rgba(245,158,11,.15)", color: "#f59e0b" }}>⚡</div>
        <div className="dchain-body">
          <div className="dchain-head">Rex · 执行</div>
          <div className="dchain-row">
            <span className="dchain-badge" style={{ color: "#fff", background: sigColor }}>{sig}</span>
            {d.confidence != null && <span className="dchain-dim">置信度 {Math.round(d.confidence * 100)}%</span>}
          </div>
          <div className="dchain-metrics">
            {d.price != null && <span>价格 <b>${d.price.toFixed(2)}</b></span>}
            {d.stop_loss != null && <span style={{ color: "#ef4444" }}>止损 <b>${d.stop_loss.toFixed(2)}</b></span>}
            {d.target_price != null && <span style={{ color: "#22c55e" }}>止盈 <b>${d.target_price.toFixed(2)}</b></span>}
          </div>
        </div>
      </div>
    </div>
  );
}
