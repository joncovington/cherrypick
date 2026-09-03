import { useEffect, useRef } from "react";
import {
  createChart,
  AreaSeries,
  LineSeries,
  type IChartApi,
  type ISeriesApi,
  type UTCTimestamp,
} from "lightweight-charts";
import { fmtMoney } from "../DataTable";
import { SERIES_COLORS } from "../Charts";

export interface TimeLineSeries {
  label: string;
  color?: string;
  points: Array<{ x: string; y: number }>;
  /** Fill down to zero (equity/underwater style) -- an rgba string, same as `Charts.tsx`'s
   *  `LineChart`. Renders as an lightweight-charts Area series instead of a Line series. */
  fill?: string;
}

/**
 * Multi-series line/area chart over a real date x-axis (`x` parses via `Date.parse`, matching
 * `CumulativeCard.tsx`'s own convention) -- the lightweight-charts equivalent of `Charts.tsx`'s
 * hand-SVG `LineChart`, for the call sites where `x` genuinely is a date (a session, a week
 * bucket, a `YYYY-MM` month) rather than a category `LineChart` was also being asked to draw
 * (a trade-sequence index, an ISO week string with no parseable date). Same mount-once/update-
 * via-setData pattern every other lightweight-charts consumer in this app uses.
 */
export function TimeLineChart({
  series,
  height = 200,
  yFormat = fmtMoney,
}: {
  series: TimeLineSeries[];
  height?: number;
  yFormat?: (v: number) => string;
}) {
  const hostRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const seriesRef = useRef<Map<string, ISeriesApi<"Area" | "Line">>>(new Map());

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
    return () => {
      chart.remove();
      chartRef.current = null;
      seriesRef.current.clear();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    const chart = chartRef.current;
    if (chart === null) return;
    const t = (x: string) => (Date.parse(x) / 1000) as UTCTimestamp;
    const lines = seriesRef.current;
    const hadNoSeriesYet = lines.size === 0;

    const wantedLabels = new Set(series.map((s) => s.label));
    for (const [label, line] of lines) {
      if (!wantedLabels.has(label)) {
        chart.removeSeries(line);
        lines.delete(label);
      }
    }

    series.forEach((s, i) => {
      const color = s.color ?? SERIES_COLORS[i % SERIES_COLORS.length]!;
      let line = lines.get(s.label);
      if (line === undefined) {
        line =
          s.fill !== undefined
            ? chart.addSeries(AreaSeries, {
                lineColor: color,
                topColor: s.fill,
                bottomColor: "rgba(0, 0, 0, 0)",
                lineWidth: 2,
                title: s.label,
                priceLineVisible: false,
              })
            : chart.addSeries(LineSeries, { color, lineWidth: 2, title: s.label, priceLineVisible: false });
        lines.set(s.label, line);
      }
      line.setData(s.points.map((p) => ({ time: t(p.x), value: p.y })));
    });

    if (hadNoSeriesYet) chart.timeScale().fitContent();
  }, [series]);

  if (series.every((s) => s.points.length < 2)) return <p className="muted">not enough history yet</p>;

  return <div ref={hostRef} style={{ height: `${String(height)}px` }} />;
}
