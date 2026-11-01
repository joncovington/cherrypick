import { useEffect, useRef } from "react";
import { createChart, LineSeries, LineStyle, type IChartApi, type ISeriesApi, type IPriceLine, type UTCTimestamp } from "lightweight-charts";
import { canvasPalette, hexOf } from "../../components/chart/cssvars";

export interface Level {
  price: number;
  title: string;
  /** A CSS token name (`--pos`), resolved for the canvas at draw time. */
  token: string;
  style?: "solid" | "dashed" | "dotted";
}

const ET = "America/New_York";

function etClock(t: number): string {
  return new Date(t * 1000).toLocaleTimeString("en-GB", { timeZone: ET, hour: "2-digit", minute: "2-digit", hour12: false });
}

const STYLE = { solid: LineStyle.Solid, dashed: LineStyle.Dashed, dotted: LineStyle.Dotted } as const;

/**
 * Today's SPX spot trail (the recorder's ~15 s samples, RTH) with the panel's levels drawn across
 * it. A line, not candles: the trail is samples, and bars built from samples would invent highs
 * and lows. The price scale fits the trail plus the levels inside `fitWithin` of spot, so a far
 * wall does not flatten the day into a line along the bottom.
 */
export function LevelsChart({
  trail,
  levels,
  spot,
  fitWithin,
  height = 420,
}: {
  trail: Array<{ ts: number; spot: number }>;
  levels: Level[];
  spot: number;
  fitWithin: number;
  height?: number;
}) {
  const hostRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const lineRef = useRef<ISeriesApi<"Line"> | null>(null);
  const priceLinesRef = useRef<IPriceLine[]>([]);
  const rangeRef = useRef<{ min: number; max: number } | null>(null);

  useEffect(() => {
    const el = hostRef.current;
    if (el === null) return;
    const pal = canvasPalette();
    const chart = createChart(el, {
      autoSize: true,
      layout: { background: { color: "transparent" }, textColor: pal.textMuted },
      grid: { vertLines: { color: pal.grid }, horzLines: { color: pal.grid } },
      rightPriceScale: { borderColor: pal.gridMid },
      // ~1,500 samples a session: the default 0.5 px minimum spacing cannot fit the day in a card, and
      // the fitted day stays fitted when the card resizes.
      timeScale: { borderColor: pal.gridMid, timeVisible: true, minBarSpacing: 0.01, lockVisibleTimeRangeOnResize: true, tickMarkFormatter: (t: number) => etClock(t) },
      localization: { timeFormatter: (t: number) => `${etClock(t)} ET`, priceFormatter: (v: number) => v.toFixed(2) },
    });
    const line = chart.addSeries(LineSeries, {
      color: hexOf("--text", "#ffffff"),
      lineWidth: 1,
      priceLineVisible: false,
      autoscaleInfoProvider: (base: () => { priceRange: { minValue: number; maxValue: number } } | null) => {
        const r = rangeRef.current;
        const b = base();
        if (r === null) return b;
        return { priceRange: { minValue: Math.min(r.min, b?.priceRange.minValue ?? r.min), maxValue: Math.max(r.max, b?.priceRange.maxValue ?? r.max) } };
      },
    });
    chartRef.current = chart;
    lineRef.current = line;
    return () => {
      chart.remove();
      chartRef.current = null;
      lineRef.current = null;
      priceLinesRef.current = [];
    };
  }, []);

  useEffect(() => {
    const line = lineRef.current;
    if (line === null) return;
    const near = levels.filter((l) => Math.abs(l.price - spot) <= fitWithin).map((l) => l.price);
    const prices = [...trail.map((t) => t.spot), ...near, spot];
    rangeRef.current = { min: Math.min(...prices), max: Math.max(...prices) };
    line.setData(trail.map((t) => ({ time: t.ts as UTCTimestamp, value: t.spot })));
    for (const pl of priceLinesRef.current) line.removePriceLine(pl);
    priceLinesRef.current = levels.map((l) =>
      line.createPriceLine({
        price: l.price,
        color: hexOf(l.token, "#888888"),
        lineWidth: 1,
        lineStyle: STYLE[l.style ?? "dashed"],
        axisLabelVisible: true,
        title: l.title,
      }),
    );
    // After layout: an auto-sized chart has no width yet on the first data pass, and a fit then
    // showed only the last hours of the session.
    const frame = requestAnimationFrame(() => chartRef.current?.timeScale().fitContent());
    return () => cancelAnimationFrame(frame);
  }, [trail, levels, spot, fitWithin]);

  return <div ref={hostRef} style={{ width: "100%", height }} />;
}
