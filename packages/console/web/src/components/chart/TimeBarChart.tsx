import { useEffect, useRef } from "react";
import {
  createChart,
  HistogramSeries,
  LineSeries,
  type IChartApi,
  type ISeriesApi,
  type UTCTimestamp,
} from "lightweight-charts";
import { fmtMoney } from "../DataTable";

/**
 * Signed bar chart over a real date x-axis, green/red by sign, with an optional line overlay --
 * the lightweight-charts equivalent of `Charts.tsx`'s hand-SVG `BarChart`, for the call sites
 * where `x` genuinely is a date (same rule `TimeLineChart.tsx` follows; an ISO-week string like
 * `2026-W35` or a trade-sequence index stays on the hand-SVG `BarChart` instead, since neither
 * parses as a real date lightweight-charts' time axis needs).
 */
export function TimeBarChart({
  bars,
  overlay,
  height = 200,
  yFormat = fmtMoney,
}: {
  bars: Array<{ x: string; y: number }>;
  overlay?: Array<{ x: string; y: number }>;
  height?: number;
  yFormat?: (v: number) => string;
}) {
  const hostRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const barsRef = useRef<ISeriesApi<"Histogram"> | null>(null);
  const overlayRef = useRef<ISeriesApi<"Line"> | null>(null);

  useEffect(() => {
    const el = hostRef.current;
    if (el === null) return;
    const chart = createChart(el, {
      autoSize: true,
      layout: { background: { color: "transparent" }, textColor: "#a6adb8" },
      grid: { vertLines: { color: "#1a1d23" }, horzLines: { color: "#1a1d23" } },
      rightPriceScale: { borderColor: "#23262d" },
      timeScale: { borderColor: "#23262d" },
      localization: { priceFormatter: yFormat },
    });
    chartRef.current = chart;
    barsRef.current = chart.addSeries(HistogramSeries, { priceLineVisible: false, lastValueVisible: false });
    overlayRef.current = chart.addSeries(LineSeries, {
      color: "#7aa2ff",
      lineWidth: 2,
      title: "cumulative",
      priceLineVisible: false,
    });
    return () => {
      chart.remove();
      chartRef.current = null;
      barsRef.current = null;
      overlayRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    const barSeries = barsRef.current;
    const overlaySeries = overlayRef.current;
    const chart = chartRef.current;
    if (barSeries === null || overlaySeries === null || chart === null) return;
    const t = (x: string) => (Date.parse(x) / 1000) as UTCTimestamp;
    const hadNoData = barSeries.data().length === 0;

    barSeries.setData(bars.map((b) => ({ time: t(b.x), value: b.y, color: b.y >= 0 ? "#43b57a" : "#d95c4a" })));
    overlaySeries.setData((overlay ?? []).map((o) => ({ time: t(o.x), value: o.y })));

    if (hadNoData) chart.timeScale().fitContent();
  }, [bars, overlay]);

  if (bars.length === 0) return <p className="muted">not enough history yet</p>;

  return <div ref={hostRef} style={{ height: `${String(height)}px` }} />;
}
