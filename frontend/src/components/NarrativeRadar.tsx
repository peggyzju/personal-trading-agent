import { useEffect, useState } from "react";
import { api, type NarrativeRadar as NarrativeRadarData, type NarrativeShockItem } from "../api/client";

function statusLabel(item: NarrativeShockItem): string {
  if (item.status === "active") return `${item.severity.toUpperCase()} · 价格确认`;
  if (item.status === "watch") return "WATCH · 部分确认";
  return "INACTIVE";
}

function statusClass(item: NarrativeShockItem): string {
  if (item.status === "active" && item.severity === "high") return "high";
  if (item.status === "active") return "active";
  if (item.status === "watch") return "watch";
  return "inactive";
}

function groupLabel(group: string): string {
  return group.replaceAll("_", " ");
}

function metricLabel(key: string): string {
  const labels: Record<string, string> = {
    hardware_1d_median_pct: "硬件1D",
    software_minus_hardware_3d_pct: "软-硬3D",
    software_breadth: "软件MA20",
    hardware_breadth: "硬件MA20",
    tlt_1d_pct: "TLT 1D",
    qqq_vs_spy_1d_pct: "QQQ-SPY",
    xle_vs_spy_1d_pct: "XLE-SPY 1D",
    xle_vs_spy_3d_pct: "XLE-SPY 3D",
    iwm_vs_spy_1d_pct: "IWM-SPY",
    hyg_1d_pct: "HYG 1D",
  };
  return labels[key] || key;
}

function formatMetric(key: string, value: number): string {
  if (key.includes("breadth")) return `${Math.round(value * 100)}%`;
  return `${value >= 0 ? "+" : ""}${value.toFixed(1)}%`;
}

function ShockCard({ item }: { item: NarrativeShockItem }) {
  const metrics = Object.entries(item.metrics || {});
  return (
    <div className={`narrative-card ${statusClass(item)}`}>
      <div className="narrative-card-head">
        <div>
          <div className="narrative-title">{item.headline}</div>
          <div className="narrative-theme">{item.theme}</div>
        </div>
        <span className={`narrative-status ${statusClass(item)}`}>{statusLabel(item)}</span>
      </div>

      <div className="narrative-summary">{item.summary}</div>

      {(item.affected_groups.length > 0 || item.beneficiary_groups.length > 0) && (
        <div className="narrative-groups">
          <div>
            <span>受压</span>
            <b>{item.affected_groups.map(groupLabel).join(" · ") || "无"}</b>
          </div>
          <div>
            <span>受益</span>
            <b>{item.beneficiary_groups.map(groupLabel).join(" · ") || "无"}</b>
          </div>
        </div>
      )}

      {metrics.length > 0 && (
        <div className="narrative-metrics">
          {metrics.slice(0, 4).map(([key, value]) => (
            <div className="narrative-metric" key={key}>
              <span>{metricLabel(key)}</span>
              <b className={value >= 0 ? "up" : "down"}>{formatMetric(key, value)}</b>
            </div>
          ))}
        </div>
      )}

      <div className="narrative-foot">
        <span>{item.price_confirmed ? "价格已确认" : item.status === "watch" ? "等待完整确认" : "未触发"}</span>
        <span>{item.action_hint === "soft_overlay_enabled" ? "新增买入 soft overlay" : "仅观察"}</span>
      </div>
    </div>
  );
}

export default function NarrativeRadar() {
  const [radar, setRadar] = useState<NarrativeRadarData | null>(null);

  useEffect(() => {
    const load = () => {
      api.getMarketNarrative().then(setRadar).catch(() => {});
    };
    load();
    const t = setInterval(load, 60000);
    return () => clearInterval(t);
  }, []);

  const items = radar?.items || [];
  const primary = items.find(i => i.status === "active") || items.find(i => i.status === "watch") || items[0];
  const monitored = items.length;

  return (
    <div className="narrative-panel">
      <div className="narrative-panel-head">
        <div>
          <span className="narrative-panel-title">市场叙事雷达</span>
          <span className="narrative-panel-sub">价格确认 · 不自动卖出</span>
        </div>
        <span className={`narrative-pill ${(radar?.active_count || 0) > 0 ? "active" : (radar?.watch_count || 0) > 0 ? "watch" : "inactive"}`}>
          {(radar?.active_count || 0) > 0 ? `${radar?.active_count} active` : (radar?.watch_count || 0) > 0 ? `${radar?.watch_count} watch` : "clear"}
        </span>
      </div>

      {primary ? (
        <>
          <ShockCard item={primary} />
          <div className="narrative-watch-row">
            {items.slice(0, 4).map(item => (
              <span className={`narrative-mini ${statusClass(item)}`} key={item.theme} title={item.reason}>
                {item.headline}
              </span>
            ))}
          </div>
        </>
      ) : (
        <div className="narrative-empty">暂无可用叙事数据。Maya 会在 8:00 ET 刷新。</div>
      )}

      <div className="narrative-note">
        监控 {monitored} 个主题；只给 Scout/Rex 上下文，不改变硬止损、BE 或追踪止盈。
      </div>
    </div>
  );
}
