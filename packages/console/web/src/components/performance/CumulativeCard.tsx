import { useEffect, useRef } from "react";
import {
  createChart,
  createSeriesMarkers,
  LineSeries,
  type IChartApi,
  type ISeriesApi,
  type ISeriesMarkersPluginApi,
  type SeriesMarker,
  type Time,
  type UTCTimestamp,
} from "lightweight-charts";
import type { ModulePerformanceGroup, MeasurementBreak } from "../../lib/api";
import { ARM_COLORS } from "../chart/tokens";

/**
 * Cumulative net P&L per profile, one line per tag -- the mount/teardown-once, update-via-setData
 * pattern `Overview/EquityCard.tsx` already established (the suite's only other lightweight-charts
 * consumer), so a 60s refetch updates the existing lines in place rather than discarding whatever
 * pan/zoom the viewer set. `fitContent()` only fires the first time data arrives, same reason.
 *
 * Break markers ride on the FIRST group's line only (not once per series -- lightweight-charts
 * attaches markers to one series, and a marker repeated on every line would double-count the same
 * date visually). The first group is deliberately the module's own first-listed tag (usually
 * `control`), not chosen by net or sample size, so which line carries the markers stays stable
 * across polls.
 */
export function CumulativeCard({ groups, breaks }: { groups: ModulePerformanceGroup[]; breaks: MeasurementBreak[] }) {
  const hostRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const seriesRef = useRef<Map<string, ISeriesApi<"Line">>>(new Map());
  const markersRef = useRef<ISeriesMarkersPluginApi<Time> | null>(null);

  useEffect(() => {
    const el = hostRef.current;
    if (el === null) return;
    const chart = createChart(el, {
      autoSize: true,
      layout: { background: { color: "transparent" }, textColor: "#a6adb8" },
      grid: { vertLines: { color: "#1a1d23" }, horzLines: { color: "#1a1d23" } },
      // Extra top margin: break markers render `aboveBar`, and with no headroom a marker aligned
      // near a line's local peak collides with the card's own header.
      rightPriceScale: { borderColor: "#23262d", scaleMargins: { top: 0.15, bottom: 0.05 } },
      timeScale: { borderColor: "#23262d" },
    });
    chartRef.current = chart;
    return () => {
      chart.remove();
      chartRef.current = null;
      seriesRef.current.clear();
      markersRef.current = null;
    };
  }, []);

  useEffect(() => {
    const chart = chartRef.current;
    if (chart === null || groups.length === 0) return;
    const t = (session: string) => (Date.parse(session + "T00:00:00Z") / 1000) as UTCTimestamp;
    const series = seriesRef.current;
    const hadNoSeriesYet = series.size === 0;

    const wantedTags = new Set(groups.map((g) => g.tag));
    for (const [tag, line] of series) {
      if (!wantedTags.has(tag)) {
        chart.removeSeries(line);
        series.delete(tag);
      }
    }

    groups.forEach((g, i) => {
      let cum = 0;
      const points = g.sessionNets.map(([session, net]) => {
        cum += net;
        return { time: t(session), value: cum };
      });
      let line = series.get(g.tag);
      if (line === undefined) {
        line = chart.addSeries(LineSeries, {
          color: ARM_COLORS[i % ARM_COLORS.length]!,
          lineWidth: 1,
          title: g.tag,
          priceLineVisible: false,
        });
        series.set(g.tag, line);
      }
      line.setData(points);

      // The first group's own series carries the break markers -- see the component docstring.
      if (i === 0) {
        const markers: SeriesMarker<Time>[] = breaks.map((b) => ({
          time: t(b.date),
          position: "aboveBar",
          color: "#d9a13b",
          shape: "circle",
          text: b.key,
        }));
        markersRef.current = createSeriesMarkers(line, markers);
      }
    });

    if (hadNoSeriesYet) chart.timeScale().fitContent();
  }, [groups, breaks]);

  return (
    <div ref={hostRef} style={{ height: "18rem" }}>
      {groups.length === 0 && <p className="muted">not enough session history yet</p>}
    </div>
  );
}
