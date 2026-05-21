import { useEffect, useState } from "react";

// ── Types ─────────────────────────────────────────────────────────────────────

interface VersionStats {
  n: number;
  needed_for_significance: number;
  is_significant: boolean;
  progress_pct: number;
  win_rate: number;
  win_rate_ci_90: [number, number];
  avg_win_pct: number;
  avg_loss_pct: number;
  profit_factor: number | null;
  total_dollar_pnl: number;
  exit_breakdown: Record<string, number>;
  entry_quality: { rsi_mean: number | null; vma20_mean: number | null };
  by_regime: Record<string, { n: number; win_rate: number; avg_pnl: number }>;
  status?: string;
}

interface VersionInfo {
  version: string;
  created_at: string;
  notes: string;
  params: {
    stop_loss_pct: number;
    max_position_pct: number;
    entry_rsi_max: number | null;
    entry_vma20_max: number | null;
  };
  stats: VersionStats;
}

interface QualityPoint {
  date: string;
  quality_score: number | null;
  rsi_mean: number | null;
  vma20_mean: number | null;
  signal_counts: Record<string, number> | null;
}

interface ValidationReport {
  current_version: VersionInfo | null;
  comparison: {
    v1: VersionInfo;
    v2: VersionInfo;
    verdict: string;
  } | null;
  all_versions: VersionInfo[];
  quality_trend: QualityPoint[];
  trade_history_count: number;
}

// ── Sub-components ────────────────────────────────────────────────────────────

function StatCell({
  label,
  value,
  good,
  bad,
  note,
}: {
  label: string;
  value: string | number | null;
  good?: boolean;
  bad?: boolean;
  note?: string;
}) {
  const color = good ? "text-green-400" : bad ? "text-red-400" : "text-white";
  return (
    <div className="flex flex-col">
      <span className="text-xs text-gray-400">{label}</span>
      <span className={`text-lg font-bold ${color}`}>
        {value ?? "—"}
      </span>
      {note && <span className="text-xs text-gray-500">{note}</span>}
    </div>
  );
}

function ProgressBar({ pct, needed, n }: { pct: number; needed: number; n: number }) {
  const filledWidth = Math.min(pct, 100);
  return (
    <div className="mt-2">
      <div className="flex justify-between text-xs text-gray-400 mb-1">
        <span>样本积累进度</span>
        <span>{n} / {needed} 笔</span>
      </div>
      <div className="h-2 bg-gray-700 rounded-full overflow-hidden">
        <div
          className={`h-full rounded-full transition-all ${
            pct >= 100 ? "bg-green-500" : pct >= 50 ? "bg-yellow-500" : "bg-blue-500"
          }`}
          style={{ width: `${filledWidth}%` }}
        />
      </div>
      {pct < 100 && (
        <p className="text-xs text-gray-500 mt-1">
          还需 {needed - n} 笔达到统计显著（90% CI）
        </p>
      )}
    </div>
  );
}

function VerdictBanner({ verdict }: { verdict: string }) {
  const isGreen  = verdict.startsWith("🟢");
  const isYellow = verdict.startsWith("🟡");
  const isRed    = verdict.startsWith("🔴");
  const bg = isGreen  ? "bg-green-900/40 border-green-700"
           : isYellow ? "bg-yellow-900/40 border-yellow-700"
           : isRed    ? "bg-red-900/40 border-red-700"
           :            "bg-blue-900/40 border-blue-700";
  return (
    <div className={`rounded-lg border p-3 text-sm ${bg}`}>
      {verdict}
    </div>
  );
}

function VersionCard({ v, label }: { v: VersionInfo; label: string }) {
  const s = v.stats;
  const noTrades = !s || s.n === 0;
  const p = v.params;

  return (
    <div className="bg-gray-800 rounded-xl p-4 flex-1 min-w-0">
      <div className="flex items-center justify-between mb-3">
        <div>
          <span className="font-bold text-white text-lg">{v.version}</span>
          <span className="ml-2 text-xs text-gray-400">{label}</span>
        </div>
        <span className="text-xs text-gray-500">{v.created_at.slice(0, 10)}</span>
      </div>
      <p className="text-xs text-gray-400 mb-3">{v.notes}</p>

      {/* Params */}
      <div className="flex gap-3 flex-wrap text-xs mb-4">
        <span className="bg-gray-700 px-2 py-0.5 rounded">止损 {p.stop_loss_pct}%</span>
        <span className="bg-gray-700 px-2 py-0.5 rounded">仓位上限 {(p.max_position_pct * 100).toFixed(0)}%</span>
        {p.entry_rsi_max && <span className="bg-gray-700 px-2 py-0.5 rounded">RSI &lt; {p.entry_rsi_max}</span>}
        {p.entry_vma20_max && <span className="bg-gray-700 px-2 py-0.5 rounded">vMA20 &lt; {p.entry_vma20_max}%</span>}
      </div>

      {noTrades ? (
        <p className="text-gray-500 text-sm">暂无平仓记录</p>
      ) : (
        <>
          <div className="grid grid-cols-2 gap-4 mb-3">
            <StatCell
              label="胜率"
              value={`${s.win_rate}%`}
              good={s.win_rate >= 50}
              bad={s.win_rate < 40}
              note={`90% CI: ${s.win_rate_ci_90[0]}–${s.win_rate_ci_90[1]}%`}
            />
            <StatCell
              label="盈亏比"
              value={s.profit_factor?.toFixed(2) ?? "—"}
              good={!!s.profit_factor && s.profit_factor >= 1.3}
              bad={!!s.profit_factor && s.profit_factor < 1.0}
            />
            <StatCell
              label="均赢"
              value={s.avg_win_pct ? `+${s.avg_win_pct}%` : "—"}
              good
            />
            <StatCell
              label="均亏"
              value={s.avg_loss_pct ? `${s.avg_loss_pct}%` : "—"}
              bad
            />
          </div>

          <ProgressBar pct={s.progress_pct} needed={s.needed_for_significance} n={s.n} />

          {/* Entry quality */}
          {s.entry_quality?.rsi_mean && (
            <div className="mt-3 text-xs text-gray-400">
              入场质量 — RSI均值 {s.entry_quality.rsi_mean}
              {s.entry_quality.vma20_mean != null && ` | vMA20均值 +${s.entry_quality.vma20_mean}%`}
            </div>
          )}

          {/* Exit breakdown */}
          <div className="mt-2 flex gap-2 flex-wrap text-xs">
            {Object.entries(s.exit_breakdown).map(([reason, cnt]) => (
              <span key={reason} className="bg-gray-700 px-2 py-0.5 rounded">
                {reason === "stop_loss" ? "止损" : reason === "target_hit" ? "达标" : reason === "time_exit" ? "超时" : reason}: {cnt}
              </span>
            ))}
          </div>
        </>
      )}
    </div>
  );
}

function QualityTrend({ points }: { points: QualityPoint[] }) {
  if (!points.length) return null;
  const max = Math.max(...points.map(p => p.quality_score ?? 0), 1);

  return (
    <div className="bg-gray-800 rounded-xl p-4">
      <h3 className="text-sm font-semibold text-gray-300 mb-3">扫描质量趋势（每日）</h3>
      <div className="flex items-end gap-1 h-20">
        {points.map((p, i) => {
          const h = ((p.quality_score ?? 0) / max) * 100;
          const color = (p.quality_score ?? 0) >= 80 ? "bg-green-500"
                      : (p.quality_score ?? 0) >= 60 ? "bg-yellow-500" : "bg-red-500";
          return (
            <div key={i} className="flex-1 flex flex-col items-center group relative">
              <div className="absolute bottom-full mb-1 hidden group-hover:block bg-gray-900 text-white text-xs rounded px-2 py-1 whitespace-nowrap z-10">
                {p.date}<br />
                质量: {p.quality_score}<br />
                RSI均: {p.rsi_mean} | vMA20均: {p.vma20_mean != null ? `+${p.vma20_mean}%` : "—"}
              </div>
              <div
                className={`w-full rounded-t ${color}`}
                style={{ height: `${h}%`, minHeight: 2 }}
              />
            </div>
          );
        })}
      </div>
      <div className="flex justify-between text-xs text-gray-500 mt-1">
        <span>{points[0]?.date}</span>
        <span>{points[points.length - 1]?.date}</span>
      </div>
    </div>
  );
}

// ── Main Component ────────────────────────────────────────────────────────────

export default function StrategyValidation() {
  const [report, setReport] = useState<ValidationReport | null>(null);
  const [loading, setLoading]   = useState(true);
  const [syncing, setSyncing]   = useState(false);
  const [error, setError]       = useState<string | null>(null);

  const fetchReport = async () => {
    setLoading(true);
    setError(null);
    try {
      const r = await fetch("/api/strategy/validation");
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      setReport(await r.json());
    } catch (e: any) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  };

  const syncTrades = async () => {
    setSyncing(true);
    try {
      const r = await fetch("/api/strategy/versions/sync", { method: "POST" });
      const d = await r.json();
      if (d.new_trades_added > 0) await fetchReport();
      alert(`同步完成：新增 ${d.new_trades_added} 条交易记录`);
    } catch {
      alert("同步失败");
    } finally {
      setSyncing(false);
    }
  };

  useEffect(() => { fetchReport(); }, []);

  if (loading) return (
    <div className="flex items-center justify-center h-64 text-gray-400">加载中…</div>
  );
  if (error) return (
    <div className="text-red-400 p-4">错误: {error}</div>
  );
  if (!report) return null;

  const { comparison, all_versions, quality_trend, trade_history_count } = report;

  return (
    <div className="space-y-5 p-4">

      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-xl font-bold text-white">策略验证仪表盘</h2>
          <p className="text-xs text-gray-400 mt-0.5">历史交易记录 {trade_history_count} 笔</p>
        </div>
        <button
          onClick={syncTrades}
          disabled={syncing}
          className="text-sm bg-blue-700 hover:bg-blue-600 disabled:opacity-50 px-3 py-1.5 rounded-lg text-white"
        >
          {syncing ? "同步中…" : "↻ 同步 Alpaca 交易"}
        </button>
      </div>

      {/* Verdict */}
      {comparison?.verdict && (
        <VerdictBanner verdict={comparison.verdict} />
      )}

      {/* Version comparison */}
      {comparison ? (
        <div className="flex gap-4">
          <VersionCard v={comparison.v1} label="上一版本" />
          <div className="flex items-center text-gray-600 text-2xl">→</div>
          <VersionCard v={comparison.v2} label="当前版本" />
        </div>
      ) : all_versions.length > 0 ? (
        <div className="flex gap-4 flex-wrap">
          {all_versions.map(v => <VersionCard key={v.version} v={v} label="" />)}
        </div>
      ) : (
        <div className="bg-gray-800 rounded-xl p-6 text-center text-gray-400">
          暂无策略版本记录
        </div>
      )}

      {/* Scan quality trend */}
      {quality_trend.length > 0 && <QualityTrend points={quality_trend} />}

      {/* All versions table */}
      {all_versions.length > 1 && (
        <div className="bg-gray-800 rounded-xl p-4">
          <h3 className="text-sm font-semibold text-gray-300 mb-3">所有版本汇总</h3>
          <div className="overflow-x-auto">
            <table className="w-full text-sm text-left">
              <thead>
                <tr className="text-xs text-gray-400 border-b border-gray-700">
                  <th className="pb-2 pr-4">版本</th>
                  <th className="pb-2 pr-4">生效日期</th>
                  <th className="pb-2 pr-4">样本数</th>
                  <th className="pb-2 pr-4">胜率</th>
                  <th className="pb-2 pr-4">90% CI</th>
                  <th className="pb-2 pr-4">盈亏比</th>
                  <th className="pb-2">显著性</th>
                </tr>
              </thead>
              <tbody>
                {all_versions.map(v => {
                  const s = v.stats;
                  const noData = !s || s.n === 0;
                  return (
                    <tr key={v.version} className="border-b border-gray-700/50">
                      <td className="py-2 pr-4 font-mono text-blue-400">{v.version}</td>
                      <td className="py-2 pr-4 text-gray-400">{v.created_at.slice(0,10)}</td>
                      <td className="py-2 pr-4">{noData ? "—" : s.n}</td>
                      <td className={`py-2 pr-4 ${!noData && s.win_rate >= 50 ? "text-green-400" : "text-red-400"}`}>
                        {noData ? "—" : `${s.win_rate}%`}
                      </td>
                      <td className="py-2 pr-4 text-gray-400 text-xs">
                        {noData ? "—" : `${s.win_rate_ci_90[0]}–${s.win_rate_ci_90[1]}%`}
                      </td>
                      <td className={`py-2 pr-4 ${!noData && (s.profit_factor ?? 0) >= 1.3 ? "text-green-400" : "text-red-400"}`}>
                        {noData ? "—" : (s.profit_factor?.toFixed(2) ?? "—")}
                      </td>
                      <td className="py-2">
                        {noData ? "—" : s.is_significant
                          ? <span className="text-green-400">✅ 显著</span>
                          : <span className="text-yellow-400">⚠️ {s.progress_pct}%</span>
                        }
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}
