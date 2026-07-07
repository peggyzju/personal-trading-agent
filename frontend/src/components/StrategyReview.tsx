import { useState, useEffect } from "react";
import { api } from "../api/client";
import type { StockDebateResult, PostmortemResult, PostmortemTrade, StrategyBacktestResult, TimelinePeriod } from "../api/client";
import type { Account, PortfolioHistory, GoalProgress, PerformanceStats, Position, PortfolioDay } from "../api/client";
import { BacktestView } from "./BacktestView";
import { DashboardSummary, CompactHeatmap } from "./PortfolioCommandCenter";

interface Props { backendOnline: boolean }

function todayET(): string {
  return new Intl.DateTimeFormat("en-CA", {
    timeZone: "America/New_York",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(new Date());
}

function monthKeyET(): string {
  return todayET().slice(0, 7);
}

function monthLabel(monthKey: string): string {
  return monthKey;
}

function toPortfolioDay(date: string, daily_pl: number): PortfolioDay {
  return { date, daily_pl, daily_return_pct: 0, equity: 0 };
}

function currentMonthDays(days: PortfolioDay[], monthKey: string): PortfolioDay[] {
  return days
    .filter(d => d.date.startsWith(monthKey))
    .sort((a, b) => a.date.localeCompare(b.date));
}

function monthStats(days: PortfolioDay[]) {
  const total = days.reduce((sum, d) => sum + d.daily_pl, 0);
  const wins = days.filter(d => d.daily_pl > 0).length;
  const losses = days.filter(d => d.daily_pl < 0).length;
  const avg = days.length ? total / days.length : 0;
  return { total, wins, losses, avg };
}

function money(value: number, decimals = 0): string {
  const sign = value >= 0 ? "+" : "-";
  return `${sign}$${Math.abs(value).toLocaleString("en-US", {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  })}`;
}

function MonthlyPLComparison({ paperDays }: { paperDays: PortfolioDay[] }) {
  const monthKey = monthKeyET();
  const [date, setDate] = useState(todayET());
  const [pl, setPl] = useState("");
  const [liveDays, setLiveDays] = useState<PortfolioDay[]>([]);
  const [saving, setSaving] = useState(false);
  const [status, setStatus] = useState<string | null>(null);
  const [formOpen, setFormOpen] = useState(false);

  useEffect(() => {
    api.getLiveDailyPL()
      .then(r => {
        setLiveDays(r.days.map(d => toPortfolioDay(d.date, d.daily_pl)));
        const today = r.days.find(d => d.date === todayET());
        if (today) setPl(String(today.daily_pl));
      })
      .catch(() => {});
  }, []);

  async function save() {
    const value = Number(pl);
    if (!date || !Number.isFinite(value)) {
      setStatus("请输入有效日期和 P/L");
      return;
    }
    setSaving(true);
    setStatus(null);
    try {
      const r = await api.saveLiveDailyPL(date, value);
      setLiveDays(r.days.map(d => toPortfolioDay(d.date, d.daily_pl)));
      setStatus("Saved");
      setTimeout(() => setStatus(null), 1800);
      setFormOpen(false);
    } catch (e: unknown) {
      setStatus(e instanceof Error ? e.message : "保存失败");
    } finally {
      setSaving(false);
    }
  }

  const paperMonth = currentMonthDays(paperDays, monthKey);
  const liveMonth = currentMonthDays(liveDays, monthKey);
  const paperStats = monthStats(paperMonth);
  const liveStats = monthStats(liveMonth);

  return (
    <section className="monthly-pl-panel">
      <div className="monthly-pl-head">
        <div>
          <div className="monthly-pl-title">当月每日收益对比</div>
          <div className="monthly-pl-subtitle">{monthLabel(monthKey)} · Paper vs Live</div>
        </div>
        <button type="button" className="monthly-pl-add" onClick={() => setFormOpen(v => !v)}>
          {formOpen ? "Close" : "+ Add Live P/L"}
        </button>
      </div>

      {formOpen && (
        <div className="monthly-pl-form">
          <label>
            <span>Date</span>
            <input type="date" value={date} onChange={e => setDate(e.target.value)} />
          </label>
          <label>
            <span>P/L</span>
            <input
              type="number"
              step="0.01"
              value={pl}
              onChange={e => setPl(e.target.value)}
              placeholder="0.00"
            />
          </label>
          <button type="button" onClick={save} disabled={saving}>
            {saving ? "Saving..." : "Save"}
          </button>
          {status && <span className="monthly-pl-status">{status}</span>}
        </div>
      )}

      <div className="monthly-pl-grid">
        <MonthlyPLCard
          title="Paper Trading"
          stats={paperStats}
          days={paperMonth}
          mode="percent"
          emptyText="本月还没有 paper 收益记录。"
        />
        <MonthlyPLCard
          title="Live Trading"
          stats={liveStats}
          days={liveMonth}
          mode="dollar"
          emptyText="本月还没有实盘 P/L 记录。"
        />
      </div>
    </section>
  );
}

function MonthlyPLCard({
  title, stats, days, mode, emptyText,
}: {
  title: string;
  stats: ReturnType<typeof monthStats>;
  days: PortfolioDay[];
  mode: "percent" | "dollar";
  emptyText: string;
}) {
  const totalClass = stats.total >= 0 ? "pos" : "neg";
  return (
    <div className="monthly-pl-card">
      <div className="monthly-pl-card-head">
        <span>{title}</span>
        <b className={totalClass}>P/L {money(stats.total)}</b>
      </div>
      <div className="monthly-pl-meta">
        <span><b className="up">{stats.wins}</b> 盈 / <b className="down">{stats.losses}</b> 亏</span>
        <span>Avg <b className={stats.avg >= 0 ? "pos" : "neg"}>{money(stats.avg)}</b>/day</span>
      </div>
      {days.length > 0 ? (
        <CompactHeatmap days={days} title="" mode={mode} compact dateFilter="none" />
      ) : (
        <div className="monthly-pl-empty">{emptyText}</div>
      )}
    </div>
  );
}

// 收益概览（从首页移来）：自取数 account / portfolio history / goal / 历史胜率 / 持仓
function PerformanceSummary() {
  const [account, setAccount] = useState<Account | null>(null);
  const [history, setHistory] = useState<PortfolioHistory | null>(null);
  const [goal, setGoal]       = useState<GoalProgress | null>(null);
  const [perf, setPerf]       = useState<PerformanceStats | null>(null);
  const [positions, setPositions] = useState<Position[]>([]);

  useEffect(() => {
    const load = () => {
      api.getAccount().then(setAccount).catch(() => {});
      api.getPortfolioHistory().then(setHistory).catch(() => {});
      api.getGoalProgress().then(setGoal).catch(() => {});
      api.getPerformanceStats().then(setPerf).catch(() => {});
      api.getPositions().then(setPositions).catch(() => {});
    };
    load();
    const t = setInterval(load, 30000);
    return () => clearInterval(t);
  }, []);

  // 当前持仓胜率/盈亏比
  const winners = positions.filter(p => p.unrealized_plpc > 0);
  const losers  = positions.filter(p => p.unrealized_plpc < 0);
  const holdWr  = positions.length ? Math.round(winners.length / positions.length * 100) : null;
  const avgWin  = winners.length ? winners.reduce((s, p) => s + p.unrealized_plpc, 0) / winners.length : 0;
  const avgLoss = losers.length  ? Math.abs(losers.reduce((s, p) => s + p.unrealized_plpc, 0) / losers.length) : 0;
  const holdPr  = avgLoss > 0 ? (avgWin / avgLoss).toFixed(1) : "∞";
  const okColor = (ok: boolean) => (ok ? "var(--green)" : "#f59e0b");

  return (
    <div className="pcc-dashboard-top perf-compact" style={{ marginBottom: 16 }}>
      <DashboardSummary goal={goal} history={history} account={account} />

      <div className="perf-stats-row">
        {perf && perf.total > 0 && (
          <span className="perf-stat-group" title={`${perf.total}笔已平仓 · 均盈+${perf.avg_win_pct}% · 均亏${perf.avg_loss_pct}%`}>
            <span className="perf-stat-tag">历史</span>
            <b style={{ color: okColor(perf.win_rate >= 50) }}>{perf.win_rate}%</b> 胜率
            <span className="perf-stat-sep">·</span>
            <b style={{ color: okColor(perf.profit_factor >= 1) }}>{perf.profit_factor.toFixed(2)}x</b> 盈亏比
            <span className="perf-stat-sep">·</span>
            {perf.total} 笔
          </span>
        )}
        {positions.length > 0 && (
          <span className="perf-stat-group" title={`${winners.length}盈/${losers.length}亏 · 均浮盈+${avgWin.toFixed(1)}% · 均浮亏-${avgLoss.toFixed(1)}%`}>
            <span className="perf-stat-tag">持仓</span>
            <b style={{ color: okColor((holdWr ?? 0) >= 50) }}>{holdWr}%</b> 胜率
            <span className="perf-stat-sep">·</span>
            <b style={{ color: okColor(parseFloat(holdPr) >= 1) }}>{holdPr}x</b> 盈亏比
            <span className="perf-stat-sep">·</span>
            {winners.length}/{positions.length} 盈/总
          </span>
        )}
      </div>

      <MonthlyPLComparison paperDays={history?.days ?? []} />
    </div>
  );
}

export function StrategyReviewPanel({ backendOnline }: Props) {
  if (!backendOnline) {
    return <div className="brief-offline">启动后端服务以查看策略复盘。</div>;
  }

  return (
    <div className="sr-container">
      <PerformanceSummary />
      <PostMortemPanel backendOnline={backendOnline} />
      <BacktestView backendOnline={backendOnline} />
    </div>
  );
}


// ── Post-Mortem Panel ──────────────────────────────────────────────────────────

const PM_PERIODS = [
  { label: "本周 7d",  days: 7 },
  { label: "双周 14d", days: 14 },
  { label: "本月 30d", days: 30 },
  { label: "本季 90d", days: 90 },
];

function PostMortemPanel({ backendOnline }: Props) {
  const [days, setDays]           = useState(7);
  const [loading, setLoading]     = useState(false);
  const [result, setResult]       = useState<PostmortemResult | null>(null);
  const [error, setError]         = useState<string | null>(null);

  async function generate() {
    if (!backendOnline) return;
    setLoading(true);
    setError(null);
    try {
      const r = await api.getPostmortem(days, 3);
      setResult(r);
      if (r.error) setError(r.error);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "请求失败");
    } finally {
      setLoading(false);
    }
  }

  return (
    <>
      <div className="sr-header">
        <div>
          <h2 className="sr-title">🔍 周度复盘</h2>
          <p className="sr-subtitle">AI 自我分析赢家/亏损特征，识别 Prompt 漏洞</p>
        </div>
      </div>

      {/* Period + generate */}
      <div className="pm-controls">
        <div className="pm-period-group">
          {PM_PERIODS.map(p => (
            <button
              key={p.days}
              className={`pm-period-btn${days === p.days ? " active" : ""}`}
              onClick={() => setDays(p.days)}
            >
              {p.label}
            </button>
          ))}
        </div>
        <button className="brief-generate-btn" onClick={generate} disabled={loading}>
          {loading ? "分析中…" : "▶ 生成复盘"}
        </button>
        {result && (
          <span className="pm-gen-time">
            生成于 {new Date(result.generated_at).toLocaleTimeString("zh-CN")}
          </span>
        )}
      </div>

      {/* Error */}
      {error && <div className="pm-error">⚠ {error}</div>}

      {/* Empty state */}
      {!result && !loading && (
        <div className="brief-empty" style={{ padding: "40px 0" }}>
          <p className="brief-empty-text">
            选择时间段，点击「生成复盘」。<br />
            Claude 会分析赢家/亏损特征，并给出 Prompt 改进建议。
          </p>
        </div>
      )}

      {loading && (
        <div className="brief-empty" style={{ padding: "40px 0" }}>
          <p className="brief-empty-text">⏳ 正在拉取持仓数据并分析…（约 20-30 秒）</p>
        </div>
      )}

      {result && !loading && (
        <>
          {/* Stats row */}
          <div className="pm-stats-row">
            {[
              { label: "时间段交易数", value: String(result.total) },
              { label: "有PnL数据", value: String(result.enriched) },
              { label: "胜率", value: result.stats.win_rate != null ? result.stats.win_rate + "%" : "—", cls: result.stats.win_rate >= 50 ? "pos" : "neg" },
              { label: "平均PnL", value: result.stats.avg_pnl != null ? fmtPct(result.stats.avg_pnl) : "—", cls: (result.stats.avg_pnl ?? 0) >= 0 ? "pos" : "neg" },
              { label: "最好/最差", value: `${fmtPct(result.stats.best_pnl)} / ${fmtPct(result.stats.worst_pnl)}`, mixed: true },
            ].map(s => (
              <div className="pm-stat-card" key={s.label}>
                <div className="pm-stat-label">{s.label}</div>
                <div className={`pm-stat-value${s.cls ? " " + s.cls : ""}`}>{s.value}</div>
              </div>
            ))}
          </div>

          {/* Tier breakdown */}
          {result.tier_breakdown && Object.values(result.tier_breakdown).some(t => t && t.count > 0) && (
            <TierBreakdown breakdown={result.tier_breakdown} />
          )}

          {/* Timeline trend */}
          {result.timeline_breakdown && result.timeline_breakdown.length > 0 && (
            <TimelineTrendPanel periods={result.timeline_breakdown} />
          )}

          {/* Winner / Loser tables */}
          {(result.winners.length > 0 || result.losers.length > 0) && (
            <div className="pm-trades-grid">
              <TradeGroup title="赢家组 🏆" trades={result.winners} isWinner />
              <TradeGroup title="亏损组 ⚠️" trades={result.losers} isWinner={false} />
            </div>
          )}

          {/* Claude analysis */}
          {result.analysis && (
            <div className="pm-analysis-card">
              <div className="pm-analysis-title">Claude 自我复盘报告</div>
              <div className="pm-analysis-body">
                {result.analysis.split("\n").map((line, i) => {
                  if (line.startsWith("## "))  return <h3 key={i} className="pm-analysis-h">{renderInline(line.slice(3))}</h3>;
                  if (line.startsWith("### ")) return <h4 key={i} className="pm-analysis-sh">{renderInline(line.slice(4))}</h4>;
                  if (line.startsWith("- "))   return <p key={i} className="pm-analysis-bullet">• {renderInline(line.slice(2))}</p>;
                  if (line.trim() === "")       return <div key={i} className="pm-analysis-spacer" />;
                  return <p key={i} className="pm-analysis-p">{renderInline(line)}</p>;
                })}
              </div>
            </div>
          )}
        </>
      )}
    </>
  );
}

function fmtPct(v: number | null | undefined): string {
  if (v == null) return "—";
  return (v >= 0 ? "+" : "") + v.toFixed(1) + "%";
}

function renderInline(text: string) {
  return text.split(/(\*\*[^*]+\*\*)/g).map((part, i) =>
    part.startsWith("**") && part.endsWith("**")
      ? <strong key={i}>{part.slice(2, -2)}</strong>
      : part
  );
}

// ── Tier Breakdown ─────────────────────────────────────────────────────────────

const TIER_META = {
  uptrend: { label: "上升趋势", emoji: "📈", cls: "pos" },
  neutral: { label: "中性",     emoji: "➡️",  cls: ""    },
  trap:    { label: "下跌陷阱", emoji: "⚠️",  cls: "neg" },
} as const;

function TierBreakdown({ breakdown }: { breakdown: PostmortemResult["tier_breakdown"] }) {
  return (
    <div className="pm-tier-card">
      <div className="pm-tier-title">策略归因 — 趋势层级分布</div>
      <div className="pm-tier-row pm-tier-header">
        <span>层级</span><span>笔数</span><span>胜率</span><span>均PnL</span>
      </div>
      {(["uptrend", "neutral", "trap"] as const).map(tier => {
        const s = breakdown[tier];
        const meta = TIER_META[tier];
        if (!s || s.count === 0) return null;
        return (
          <div className={`pm-tier-row${tier === "trap" && (s.avg_pnl ?? 0) < -0.5 ? " pm-tier-trap-alert" : ""}`} key={tier}>
            <span>{meta.emoji} {meta.label}</span>
            <span>{s.count}</span>
            <span className={s.win_rate != null && s.win_rate >= 50 ? "pos" : "neg"}>
              {s.win_rate != null ? s.win_rate + "%" : "—"}
            </span>
            <span className={meta.cls || ((s.avg_pnl ?? 0) >= 0 ? "pos" : "neg")}>
              {fmtPct(s.avg_pnl)}
            </span>
          </div>
        );
      })}
    </div>
  );
}


// ── Timeline Trend Panel ──────────────────────────────────────────────────────

const TREND_META = {
  up:   { label: "↑ 改善", cls: "badge-up"   },
  down: { label: "↓ 下滑", cls: "badge-down" },
  flat: { label: "— 持平", cls: "badge-flat" },
  base: { label: "— 基准", cls: "badge-flat" },
} as const;

function TimelineTrendPanel({ periods }: { periods: TimelinePeriod[] }) {
  return (
    <div className="pm-timeline-card">
      <div className="pm-tier-title">时间段趋势分析 — 实盘是否在进步</div>
      <div className="pm-timeline-header">
        <span>周期</span><span>笔数</span><span>胜率</span><span>均 PnL</span><span>期望值 EV</span><span>趋势</span>
      </div>
      {periods.map((p, i) => {
        const tm = TREND_META[p.trend] ?? TREND_META.flat;
        return (
          <div className="pm-timeline-row" key={i}>
            <span>{p.label}</span>
            <span>{p.count > 0 ? p.count : "—"}</span>
            <span className={p.win_rate != null ? (p.win_rate >= 50 ? "pos" : "neg") : ""}>
              {p.win_rate != null ? p.win_rate + "%" : "—"}
            </span>
            <span className={p.avg_pnl != null ? (p.avg_pnl >= 0 ? "pos" : "neg") : ""}>
              {fmtPct(p.avg_pnl)}
            </span>
            <span className={p.ev != null ? (p.ev >= 0 ? "pos" : "neg") : ""}>
              {fmtPct(p.ev)}
            </span>
            <span><span className={`pm-badge ${tm.cls}`}>{tm.label}</span></span>
          </div>
        );
      })}
    </div>
  );
}


// ── Backtest Panel ─────────────────────────────────────────────────────────────

const BT_MONTHS = [
  { label: "1 个月", months: 1 },
  { label: "3 个月", months: 3 },
  { label: "6 个月", months: 6 },
];

export function BacktestPanel() {
  const [months, setMonths]   = useState(3);
  const [running, setRunning] = useState(false);
  const [result, setResult]   = useState<StrategyBacktestResult | null>(null);
  const [error, setError]     = useState<string | null>(null);

  // Load last result on mount and poll while running
  useEffect(() => {
    api.getStrategyBacktestStatus().then(s => {
      if (s.last_result) setResult(s.last_result);
      if (s.running) setRunning(true);
    }).catch(() => {});
  }, []);

  useEffect(() => {
    if (!running) return;
    const id = setInterval(async () => {
      try {
        const s = await api.getStrategyBacktestStatus();
        if (!s.running) {
          setRunning(false);
          if (s.last_result) setResult(s.last_result);
          clearInterval(id);
        }
      } catch { /* silent */ }
    }, 5000);
    return () => clearInterval(id);
  }, [running]);

  async function runBacktest() {
    setError(null);
    setRunning(true);
    try {
      await api.runStrategyBacktest(months);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "启动失败");
      setRunning(false);
    }
  }

  const versions = result?.versions ?? [];
  const deltas   = result?.deltas   ?? [];
  const tierBd   = result?.tier_breakdown;

  return (
    <div className="pm-backtest-section">
      <div className="sr-header" style={{ marginTop: 32 }}>
        <div>
          <h2 className="sr-title">📊 策略版本回测</h2>
          <p className="sr-subtitle">V2 基准 vs V12 去陷阱 vs V13 纯上升趋势 — 找出关键策略改进点</p>
        </div>
      </div>

      <div className="pm-controls" style={{ marginTop: 16 }}>
        <div className="pm-period-group">
          {BT_MONTHS.map(m => (
            <button
              key={m.months}
              className={`pm-period-btn${months === m.months ? " active" : ""}`}
              onClick={() => setMonths(m.months)}
              disabled={running}
            >
              {m.label}
            </button>
          ))}
        </div>
        <button className="brief-generate-btn" onClick={runBacktest} disabled={running}>
          {running ? "⏳ 回测中…（约 2-3 分钟）" : "▶ 运行回测"}
        </button>
        {result?.generated_at && !running && (
          <span className="pm-gen-time">
            生成于 {new Date(result.generated_at).toLocaleTimeString("zh-CN")}
          </span>
        )}
      </div>

      {error && <div className="pm-error">⚠ {error}</div>}

      {running && (
        <div className="brief-empty" style={{ padding: "32px 0" }}>
          <p className="brief-empty-text">⏳ 正在下载 {result?.universe_size ?? "~50"} 只股票数据并跑回测…（约 2-3 分钟）</p>
        </div>
      )}

      {!running && result?.status === "error" && (
        <div className="pm-error">回测失败：{result.error}</div>
      )}

      {!running && result?.status === "done" && (
        <>
          {/* Version comparison table — 6 cols, no profit_factor */}
          <div className="pm-bt-table-wrap">
            <div className="pm-bt-header-row">
              <span>版本</span><span>笔数</span><span>胜率</span>
              <span>均盈</span><span>均亏</span><span>期望值 EV</span>
            </div>
            {versions.map((v, i) => (
              <div className={`pm-bt-data-row${i === 0 ? " pm-bt-baseline" : ""}`} key={v.label}>
                <span className="pm-bt-version-label">{v.label}</span>
                <span className="pm-bt-cell-muted">{v.n}</span>
                <span>{v.win_rate != null ? v.win_rate + "%" : "—"}</span>
                <span className="pos">{v.avg_win != null ? fmtPct(v.avg_win) : "—"}</span>
                <span className="neg">{v.avg_loss != null ? fmtPct(v.avg_loss) : "—"}</span>
                <span className={`pm-bt-ev${(v.exp_value ?? 0) >= 0 ? " pos" : " neg"}`}>
                  {v.exp_value != null ? fmtPct(v.exp_value) : "—"}
                </span>
              </div>
            ))}
          </div>

          {/* Tier cards */}
          {tierBd && (
            <div className="pm-bt-tier-cards">
              <div className="pm-bt-section-label">V2 基准 — 趋势层级分布</div>
              <div className="pm-bt-tier-card-row">
                {(["uptrend", "neutral", "trap"] as const).map(tier => {
                  const s = tierBd[tier];
                  const meta = TIER_META[tier];
                  const ev = (s as { ev?: number })?.ev;
                  if (!s || s.count === 0) return null;
                  return (
                    <div className={`pm-bt-tier-card${tier === "trap" ? " pm-bt-tier-card--trap" : tier === "uptrend" ? " pm-bt-tier-card--up" : ""}`} key={tier}>
                      <div className="pm-bt-tier-card-name">{meta.emoji} {meta.label}</div>
                      <div className="pm-bt-tier-card-count">{s.count} 笔</div>
                      <div className="pm-bt-tier-card-row2">
                        <span className="pm-bt-tier-card-label">胜率</span>
                        <span className={s.win_rate != null && s.win_rate >= 50 ? "pos" : "neg"}>
                          {s.win_rate != null ? s.win_rate + "%" : "—"}
                        </span>
                      </div>
                      <div className="pm-bt-tier-card-row2">
                        <span className="pm-bt-tier-card-label">EV</span>
                        <span className={`pm-bt-ev${(ev ?? 0) >= 0 ? " pos" : " neg"}`}>{fmtPct(ev)}</span>
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>
          )}

          {/* Delta callouts */}
          {deltas.length > 0 && (
            <div className="pm-bt-delta-callouts">
              <div className="pm-bt-section-label">过滤器 Delta 分析</div>
              {deltas.map(d => {
                const isPos  = d.ev_delta > 0.05;
                const isNeg  = d.ev_delta < -0.05;
                const flag   = isPos ? "✓" : isNeg ? "✗" : "≈";
                const cls    = isPos ? "pos" : isNeg ? "neg" : "pm-bt-cell-muted";
                return (
                  <div className={`pm-bt-callout${isPos ? " pm-bt-callout--pos" : isNeg ? " pm-bt-callout--neg" : ""}`} key={d.desc}>
                    <span className={`pm-bt-callout-flag ${cls}`}>{flag}</span>
                    <div className="pm-bt-callout-body">
                      <div className="pm-bt-callout-desc">{d.desc}</div>
                      <div className="pm-bt-callout-stats">
                        <span>笔数 {d.n_before}→{d.n_after} ({d.n_after - d.n_before})</span>
                        <span>胜率 {d.wr_before?.toFixed(1)}%→{d.wr_after?.toFixed(1)}%</span>
                        <span>EV {fmtPct(d.ev_before)}→{fmtPct(d.ev_after)}</span>
                      </div>
                    </div>
                    <div className={`pm-bt-callout-ev ${cls}`}>Δ {fmtPct(d.ev_delta)}</div>
                  </div>
                );
              })}
            </div>
          )}

          <div className="pm-gen-time" style={{ marginTop: 8 }}>
            回测周期：{result.period} | 股票池：{result.universe_size} 只
          </div>
        </>
      )}

      {!running && !result && (
        <p className="sr-subtitle" style={{ marginTop: 20, color: "var(--muted)" }}>
          选择时间段，点击「运行回测」。对比 V2 基准 / V12 去陷阱 / V13 上升趋势三个版本的期望值，找出哪个过滤规则真正有效。
        </p>
      )}
    </div>
  );
}

function TradeGroup({ title, trades, isWinner }: { title: string; trades: PostmortemTrade[]; isWinner: boolean }) {
  return (
    <div className={`pm-trade-group${isWinner ? " pm-winners" : " pm-losers"}`}>
      <div className="pm-trade-group-title">{title}</div>
      {trades.length === 0 ? (
        <div className="pm-trade-empty">无数据</div>
      ) : (
        trades.map((t, i) => <TradeRow key={i} t={t} isWinner={isWinner} />)
      )}
    </div>
  );
}

function TradeRow({ t }: { t: PostmortemTrade; isWinner: boolean }) {
  const [expanded, setExpanded] = useState(false);
  const pnlCls = (t.pnl_pct ?? 0) >= 0 ? "pos" : "neg";
  return (
    <div className="pm-trade-row">
      <div className="pm-trade-top">
        <span className="pm-trade-sym">{t.symbol}</span>
        {t.pnl_source === "live" && <span className="pm-live-tag">浮动</span>}
        <span className={`pm-trade-pnl ${pnlCls}`}>{fmtPct(t.pnl_pct)}</span>
      </div>
      <div className="pm-trade-meta">
        信号: {t.signal ?? "—"} | 置信度: {t.confidence ?? "—"} | RSI: {t.rsi ?? "—"} | 5d动量: {fmtPct(t.momentum_5d)}
      </div>
      <div className="pm-trade-meta">
        入场 ${t.fill_price?.toFixed(2) ?? "—"} → 现价 ${t.current_price ?? "—"} | 止损 ${t.stop_loss?.toFixed(2) ?? "—"} | 目标 ${t.target_price?.toFixed(2) ?? "—"}
      </div>
      {t.reason && (
        <>
          <button className="pm-reason-toggle" onClick={() => setExpanded(e => !e)}>
            {expanded ? "▲ 收起判断" : "▶ 当时判断"}
          </button>
          {expanded && <div className="pm-reason-text">{t.reason}</div>}
        </>
      )}
    </div>
  );
}


const VERDICT_COLOR: Record<string, string> = {
  STRONG_BUY: "#22c55e", BUY: "#86efac",
  SELL: "#f87171", STRONG_SELL: "#ef4444", AVOID: "#ef4444",
};

// ── Per-Stock Debate Panel ────────────────────────────────────────────────────

export function StockDebatePanel({
  symbol, action, context,
}: {
  symbol: string;
  action: "BUY" | "HOLD" | "SELL";
  context: Record<string, unknown>;
}) {
  const [open, setOpen]       = useState(false);
  const [result, setResult]   = useState<StockDebateResult | null>(null);
  const [loading, setLoading] = useState(false);

  async function run() {
    if (result) { setOpen(o => !o); return; }
    setOpen(true);
    setLoading(true);
    try {
      const r = await api.debateStock(symbol, action, context);
      setResult(r);
    } catch { /* silent */ }
    finally { setLoading(false); }
  }

  const verdictColor = result ? (VERDICT_COLOR[result.verdict] ?? "#f59e0b") : undefined;
  const actionLabel  = action === "BUY" ? "买入" : action === "SELL" ? "卖出" : "持有";

  return (
    <div className="stock-debate-wrap">
      <button className="sr-iter-action-btn sia-debate" onClick={run} disabled={loading && !open}>
        {loading ? "⏳ 辩论中…" : result ? (open ? "收起辩论" : "⚡ 查看辩论") : `⚡ Agent 辩论 ${actionLabel}`}
      </button>

      {open && (
        <div className="sr-debate-panel" style={{ marginTop: 8 }}>
          {loading && <div className="sr-debate-loading">3 个 Agent 正在分析 {symbol} 的{actionLabel}决策…</div>}
          {result && (
            <>
              <div className="sr-debate-side sr-debate-pro">
                <div className="sr-debate-label">📈 交易 Agent (Rex)</div>
                <p className="sr-debate-text">{result.trading_agent}</p>
              </div>
              <div className="sr-debate-side sr-debate-con">
                <div className="sr-debate-label">📊 回测 Agent</div>
                <p className="sr-debate-text">{result.backtest_agent}</p>
              </div>
              <div className="sr-debate-synthesis">
                <div className="sr-debate-label">⚖️ 复盘 Agent</div>
                <p className="sr-debate-text">{result.review_agent}</p>
                <div className="sr-debate-verdict">
                  判决：<span style={{ color: verdictColor, fontWeight: 700 }}>{result.verdict}</span>
                  <span className="sr-debate-confidence">置信度 {Math.round(result.confidence * 100)}%</span>
                </div>
                {result.key_risk && (
                  <div className="sr-debate-risk">⚠️ 主要风险：{result.key_risk}</div>
                )}
              </div>
            </>
          )}
        </div>
      )}
    </div>
  );
}
