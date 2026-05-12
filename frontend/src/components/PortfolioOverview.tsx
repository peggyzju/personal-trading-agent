import { useState, useEffect, useRef } from "react";
import { api } from "../api/client";
import type { PortfolioHistory, PortfolioDay } from "../api/client";

type Period = "1W" | "1M" | "3M" | "YTD" | "All";

interface Props { backendOnline: boolean }

// ── Helpers ──────────────────────────────────────────────────────────────────

function filterByPeriod(days: PortfolioDay[], period: Period): PortfolioDay[] {
  if (!days.length) return [];
  const today = new Date();
  const cutoff = new Date(today);
  if (period === "1W") cutoff.setDate(today.getDate() - 7);
  else if (period === "1M") cutoff.setMonth(today.getMonth() - 1);
  else if (period === "3M") cutoff.setMonth(today.getMonth() - 3);
  else if (period === "YTD") cutoff.setMonth(0, 1);
  else return days;
  return days.filter(d => new Date(d.date) >= cutoff);
}

function periodReturn(days: PortfolioDay[]) {
  if (days.length < 2) return { pl: 0, pct: 0 };
  const first = days[0].equity - days[0].daily_pl;
  const last  = days[days.length - 1].equity;
  const pl  = last - first;
  const pct = first > 0 ? (pl / first) * 100 : 0;
  return { pl, pct };
}

function sharpe(days: PortfolioDay[]): number {
  if (days.length < 5) return 0;
  const rets = days.map(d => d.daily_return_pct / 100);
  const mean = rets.reduce((a, b) => a + b, 0) / rets.length;
  const std  = Math.sqrt(rets.map(r => (r - mean) ** 2).reduce((a, b) => a + b, 0) / rets.length);
  return std > 0 ? parseFloat(((mean / std) * Math.sqrt(252)).toFixed(2)) : 0;
}

function fmt(n: number, digits = 0) {
  return Math.abs(n).toLocaleString("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

function sign(n: number) { return n >= 0 ? "+" : "−"; }

function cellColor(pct: number): string {
  if (pct >  1.0) return "#16a34a";
  if (pct >  0.1) return "#4ade80";
  if (pct > -0.1) return "#374151";
  if (pct > -1.0) return "#fca5a5";
  return "#dc2626";
}

// Group trading days into {weekKey → {dayOfWeek(0=Mon) → day}}
function buildCalendar(days: PortfolioDay[]) {
  const map = new Map<number, Map<number, PortfolioDay>>();
  for (const day of days) {
    const d = new Date(day.date);
    const dow = (d.getDay() + 6) % 7; // 0=Mon…4=Fri
    const monday = new Date(d);
    monday.setDate(d.getDate() - dow);
    monday.setHours(0, 0, 0, 0);
    const key = monday.getTime();
    if (!map.has(key)) map.set(key, new Map());
    map.get(key)!.set(dow, day);
  }
  return Array.from(map.entries())
    .sort((a, b) => a[0] - b[0])
    .map(([key, days]) => ({ monday: new Date(key), days }));
}

// ── Sparkline ─────────────────────────────────────────────────────────────────

function Sparkline({ days, positive }: { days: PortfolioDay[]; positive: boolean }) {
  if (days.length < 2) return null;
  const W = 800; const H = 72; const pad = 2;
  const equities = days.map(d => d.equity);
  const min = Math.min(...equities);
  const max = Math.max(...equities);
  const range = max - min || 1;
  const color = positive ? "#00C805" : "#FF5000";
  const gradId = `sg-${positive ? "g" : "r"}`;

  const pts = equities.map((e, i) => {
    const x = pad + (i / (equities.length - 1)) * (W - 2 * pad);
    const y = H - pad - ((e - min) / range) * (H - 2 * pad);
    return [x, y] as [number, number];
  });

  const linePath = pts.map((p, i) => `${i === 0 ? "M" : "L"}${p[0].toFixed(1)},${p[1].toFixed(1)}`).join(" ");
  const fillPath = `${linePath} L${pts[pts.length - 1][0].toFixed(1)},${H} L${pts[0][0].toFixed(1)},${H} Z`;

  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="sparkline-svg" preserveAspectRatio="none">
      <defs>
        <linearGradient id={gradId} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor={color} stopOpacity="0.25" />
          <stop offset="100%" stopColor={color} stopOpacity="0" />
        </linearGradient>
      </defs>
      <path d={fillPath} fill={`url(#${gradId})`} />
      <path d={linePath} fill="none" stroke={color} strokeWidth="1.5" />
    </svg>
  );
}

// ── Calendar Heatmap ──────────────────────────────────────────────────────────

function CalendarHeatmap({ days }: { days: PortfolioDay[] }) {
  const [tooltip, setTooltip] = useState<{ day: PortfolioDay; x: number; y: number } | null>(null);
  const ref = useRef<HTMLDivElement>(null);

  const yearDays = days.filter(d => new Date(d.date).getFullYear() === new Date().getFullYear());
  const weeks = buildCalendar(yearDays);
  const today = new Date().toISOString().slice(0, 10);

  // Month label positions
  const monthLabels: { label: string; col: number }[] = [];
  weeks.forEach((w, i) => {
    const m = w.monday.toLocaleString("en-US", { month: "short" });
    if (i === 0 || w.monday.getDate() <= 7) {
      if (!monthLabels.length || monthLabels[monthLabels.length - 1].label !== m) {
        monthLabels.push({ label: m, col: i });
      }
    }
  });

  const CELL = 13; const GAP = 3;

  function handleMouseEnter(day: PortfolioDay, e: React.MouseEvent) {
    const rect = ref.current?.getBoundingClientRect();
    if (!rect) return;
    setTooltip({ day, x: e.clientX - rect.left, y: e.clientY - rect.top });
  }

  return (
    <div className="calendar-wrap" ref={ref}>
      <h3 className="calendar-title">Daily Returns — {new Date().getFullYear()}</h3>

      {/* Month labels */}
      <div className="calendar-months" style={{ paddingLeft: 20 }}>
        {monthLabels.map((m, i) => (
          <span key={i} className="calendar-month-label"
            style={{ left: m.col * (CELL + GAP) }}>
            {m.label}
          </span>
        ))}
      </div>

      <div className="calendar-grid-wrap">
        {/* Day of week labels */}
        <div className="calendar-dow">
          {["M", "T", "W", "T", "F"].map((l, i) => (
            <span key={i} className="calendar-dow-label"
              style={{ top: i * (CELL + GAP) }}>
              {i % 2 === 0 ? l : ""}
            </span>
          ))}
        </div>

        {/* Cells */}
        <div className="calendar-grid">
          {weeks.map((week, wi) => (
            <div key={wi} className="calendar-col">
              {[0, 1, 2, 3, 4].map(dow => {
                const day = week.days.get(dow);
                const isToday = day?.date === today;
                return (
                  <div
                    key={dow}
                    className={`calendar-cell${isToday ? " today" : ""}`}
                    style={{ background: day ? cellColor(day.daily_return_pct) : "#1e2130" }}
                    onMouseEnter={day ? (e) => handleMouseEnter(day, e) : undefined}
                    onMouseLeave={() => setTooltip(null)}
                  />
                );
              })}
            </div>
          ))}
        </div>
      </div>

      {/* Legend */}
      <div className="calendar-legend">
        {[["< −1%", "#dc2626"], ["−1~0%", "#fca5a5"], ["≈ 0%", "#374151"], ["0~+1%", "#4ade80"], ["> +1%", "#16a34a"]].map(([label, color]) => (
          <span key={label} className="legend-item">
            <span className="legend-swatch" style={{ background: color }} />
            {label}
          </span>
        ))}
      </div>

      {/* Tooltip */}
      {tooltip && (
        <div className="cal-tooltip" style={{ left: tooltip.x + 12, top: tooltip.y - 60 }}>
          <div className="cal-tooltip-date">
            {new Date(tooltip.day.date + "T12:00:00").toLocaleDateString("en-US", { weekday: "short", month: "short", day: "numeric" })}
          </div>
          <div className={`cal-tooltip-pl ${tooltip.day.daily_pl >= 0 ? "pos" : "neg"}`}>
            {sign(tooltip.day.daily_pl)}${fmt(tooltip.day.daily_pl, 0)}
            <span className="cal-tooltip-pct">
              {" "}{sign(tooltip.day.daily_return_pct)}{Math.abs(tooltip.day.daily_return_pct).toFixed(2)}%
            </span>
          </div>
          <div className="cal-tooltip-equity">${fmt(tooltip.day.equity, 0)}</div>
        </div>
      )}
    </div>
  );
}

// ── Main Component ────────────────────────────────────────────────────────────

export function PortfolioOverview({ backendOnline }: Props) {
  const [data, setData] = useState<PortfolioHistory | null>(null);
  const [period, setPeriod] = useState<Period>("YTD");

  useEffect(() => {
    if (backendOnline) api.getPortfolioHistory().then(setData).catch(() => {});
  }, [backendOnline]);

  if (!data) return null;

  const filtered = filterByPeriod(data.days, period);
  const { pl, pct } = periodReturn(filtered);
  const positive = pl >= 0;
  const color = positive ? "#00C805" : "#FF5000";

  // Stats
  const todayDay  = data.days[data.days.length - 1];
  const weekDays  = filterByPeriod(data.days, "1W");
  const monthDays = filterByPeriod(data.days, "1M");
  const weekPl    = weekDays.reduce((s, d) => s + d.daily_pl, 0);
  const monthPl   = monthDays.reduce((s, d) => s + d.daily_pl, 0);
  const sh        = sharpe(filtered);

  const PERIODS: Period[] = ["1W", "1M", "3M", "YTD", "All"];

  return (
    <div className="portfolio-overview">
      {/* Hero */}
      <div className="portfolio-hero">
        <div className="portfolio-hero-left">
          <div className="portfolio-equity">
            ${data.current_equity.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
          </div>
          <div className="portfolio-return" style={{ color }}>
            {sign(pl)}${fmt(pl, 0)}&nbsp;&nbsp;
            {sign(pct)}{Math.abs(pct).toFixed(2)}%
            <span className="portfolio-period-label">{period === "All" ? " all time" : ` ${period}`}</span>
          </div>
        </div>
        {data.source === "demo" && (
          <span className="demo-badge">Demo data</span>
        )}
      </div>

      {/* Sparkline */}
      <div className="sparkline-wrap">
        <Sparkline days={filtered} positive={positive} />
      </div>

      {/* Period pills */}
      <div className="period-pills">
        {PERIODS.map(p => (
          <button key={p} className={`period-pill${period === p ? " active" : ""}`}
            style={period === p ? { color, borderColor: color } : {}}
            onClick={() => setPeriod(p)}>
            {p}
          </button>
        ))}
      </div>

      {/* Stats row */}
      <div className="portfolio-stats-row">
        <StatBox label="Today" pl={todayDay?.daily_pl ?? 0} pct={todayDay?.daily_return_pct ?? 0} />
        <StatBox label="This Week" pl={weekPl} pct={weekDays.length ? weekPl / (weekDays[0].equity - weekDays[0].daily_pl) * 100 : 0} />
        <StatBox label="This Month" pl={monthPl} pct={monthDays.length ? monthPl / (monthDays[0].equity - monthDays[0].daily_pl) * 100 : 0} />
        <div className="stat-box">
          <span className="stat-box-label">Sharpe ({period})</span>
          <span className="stat-box-value" style={{ color: sh >= 1.5 ? "#00C805" : sh >= 0.8 ? "#f59e0b" : "#FF5000" }}>
            {sh.toFixed(2)}
          </span>
        </div>
      </div>

      {/* Calendar */}
      <CalendarHeatmap days={data.days} />
    </div>
  );
}

function StatBox({ label, pl, pct }: { label: string; pl: number; pct: number }) {
  const color = pl >= 0 ? "#00C805" : "#FF5000";
  return (
    <div className="stat-box">
      <span className="stat-box-label">{label}</span>
      <span className="stat-box-value" style={{ color }}>
        {sign(pl)}${fmt(pl, 0)}
      </span>
      <span className="stat-box-sub" style={{ color }}>
        {sign(pct)}{Math.abs(pct).toFixed(2)}%
      </span>
    </div>
  );
}
