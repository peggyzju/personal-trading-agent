import { useEffect, useRef, useState } from "react";
import {
  createChart, ColorType, CandlestickSeries, LineSeries, HistogramSeries,
  type IChartApi,
} from "lightweight-charts";

interface Bar { t: string; o: number; h: number; l: number; c: number; v: number; }

// 简单移动均线（不足窗口返回 null）
function sma(values: number[], period: number): (number | null)[] {
  const out: (number | null)[] = [];
  let sum = 0;
  for (let i = 0; i < values.length; i++) {
    sum += values[i];
    if (i >= period) sum -= values[i - period];
    out.push(i >= period - 1 ? sum / period : null);
  }
  return out;
}

/** 日线 K 线图：蜡烛 + MA20/MA50 + 成交量 + 买入/止损/止盈水平线 */
export function CandleChart({ symbol, entryPrice, stopLoss, targetPrice }: {
  symbol: string;
  entryPrice?: number | null;
  stopLoss?: number | null;
  targetPrice?: number | null;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    let chart: IChartApi | null = null;
    let cancelled = false;
    let cleanupResize = () => {};

    (async () => {
      try {
        const res = await fetch(`/api/ohlcv/${symbol}?days=130`);
        const data = await res.json();
        const bars: Bar[] = data.bars ?? [];
        if (cancelled || !ref.current) return;
        if (bars.length === 0) { setErr("：无数据"); setLoading(false); return; }

        chart = createChart(ref.current, {
          width: ref.current.clientWidth,
          height: 320,
          layout: { background: { type: ColorType.Solid, color: "transparent" }, textColor: "#8b93a2", fontSize: 11 },
          grid: { vertLines: { color: "#1e2128" }, horzLines: { color: "#1e2128" } },
          rightPriceScale: { borderColor: "#262a33", scaleMargins: { top: 0.08, bottom: 0.25 } },
          timeScale: { borderColor: "#262a33", rightOffset: 12, barSpacing: 6 },
          crosshair: { mode: 0 },
        });

        const candle = chart.addSeries(CandlestickSeries, {
          upColor: "#22c55e", downColor: "#ef4444", borderVisible: false,
          wickUpColor: "#22c55e", wickDownColor: "#ef4444",
        });
        candle.setData(bars.map(b => ({ time: b.t, open: b.o, high: b.h, low: b.l, close: b.c })));

        const closes = bars.map(b => b.c);
        const ma20 = sma(closes, 20);
        const ma50 = sma(closes, 50);
        const lineData = (arr: (number | null)[]) =>
          bars.map((b, i) => (arr[i] != null ? { time: b.t, value: arr[i] as number } : null))
              .filter(Boolean) as { time: string; value: number }[];
        const ma20s = chart.addSeries(LineSeries, { color: "#f59e0b", lineWidth: 1, priceLineVisible: false, lastValueVisible: false });
        ma20s.setData(lineData(ma20));
        const ma50s = chart.addSeries(LineSeries, { color: "#a78bfa", lineWidth: 1, priceLineVisible: false, lastValueVisible: false });
        ma50s.setData(lineData(ma50));

        const vol = chart.addSeries(HistogramSeries, { priceScaleId: "vol", priceFormat: { type: "volume" } });
        vol.setData(bars.map(b => ({ time: b.t, value: b.v, color: b.c >= b.o ? "#22c55e44" : "#ef444444" })));
        chart.priceScale("vol").applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } });

        // axisLabelVisible:false —— 买入/止损/止盈 的右轴价格标签会堆叠盖住最新蜡烛;
        // 图例 + 横向虚线已能表达,右轴只保留现价标签。
        const addLine = (price: number | null | undefined, color: string, title: string) => {
          if (price != null && price > 0) {
            candle.createPriceLine({ price, color, lineWidth: 1, lineStyle: 2, axisLabelVisible: false, title });
          }
        };
        addLine(entryPrice, "#60a5fa", "买入");
        addLine(stopLoss, "#ef4444", "止损");
        addLine(targetPrice, "#22c55e", "止盈");

        // 不用 fitContent —— 它会把最新蜡烛贴死右轴、被价格标签盖住;
        // 改用 rightOffset 给最新数据右侧留白,展示最近 ~100 根。
        chart.timeScale().scrollToPosition(0, false);
        setLoading(false);

        const onResize = () => { if (ref.current && chart) chart.applyOptions({ width: ref.current.clientWidth }); };
        window.addEventListener("resize", onResize);
        cleanupResize = () => window.removeEventListener("resize", onResize);
      } catch (e) {
        if (!cancelled) { setErr("加载失败"); setLoading(false); }
      }
    })();

    return () => { cancelled = true; cleanupResize(); if (chart) chart.remove(); };
  }, [symbol, entryPrice, stopLoss, targetPrice]);

  return (
    <div className="candle-wrap">
      <div className="candle-legend">
        <span style={{ color: "#f59e0b" }}>— MA20</span>
        <span style={{ color: "#a78bfa" }}>— MA50</span>
        {entryPrice ? <span style={{ color: "#60a5fa" }}>… 买入</span> : null}
        {stopLoss ? <span style={{ color: "#ef4444" }}>… 止损</span> : null}
        {targetPrice ? <span style={{ color: "#22c55e" }}>… 止盈</span> : null}
      </div>
      <div ref={ref} className="candle-chart" />
      {(loading || err) && <div className="candle-status">K 线{err ?? "加载中…"}</div>}
    </div>
  );
}
