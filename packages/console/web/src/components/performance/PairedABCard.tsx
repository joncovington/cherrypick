import { useEffect, useRef } from "react";
import { createChart, LineSeries, type IChartApi, type ISeriesApi, type UTCTimestamp } from "lightweight-charts";
import type { AdvisedPair, ModulePerformanceGroup } from "../../lib/api";
import { Card } from "../DataTable";

/**
 * One `advised:<base>` twin against its control: both cumulative curves, plus their difference
 * (advised minus base, positive = the overlay is ahead) -- the paired-experiment counterpart to
 * `CumulativeCard.tsx`'s per-tag view. Same mount-once/update-via-setData pattern as
 * `Overview/EquityCard.tsx` and `CumulativeCard.tsx`.
 *
 * The difference line is defined only on sessions BOTH books actually recorded a net for --
 * `pair.sessionsPaired`'s own definition (`readers/pairs.ts`), not every session either book has
 * on file. A date only the advised book (or only the control) traded says nothing about how the
 * overlay compares that day.
 */
export function PairedABCard({
  pair,
  advisedGroup,
  baseGroup,
}: {
  pair: AdvisedPair;
  advisedGroup: ModulePerformanceGroup;
  baseGroup: ModulePerformanceGroup;
}) {
  const hostRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const advisedLineRef = useRef<ISeriesApi<"Line"> | null>(null);
  const baseLineRef = useRef<ISeriesApi<"Line"> | null>(null);
  const diffLineRef = useRef<ISeriesApi<"Line"> | null>(null);

  useEffect(() => {
    const el = hostRef.current;
    if (el === null) return;
    const chart = createChart(el, {
      autoSize: true,
      layout: { background: { color: "transparent" }, textColor: "#a6adb8" },
      grid: { vertLines: { color: "#1a1d23" }, horzLines: { color: "#1a1d23" } },
      rightPriceScale: { borderColor: "#23262d" },
      timeScale: { borderColor: "#23262d" },
    });
    chartRef.current = chart;
    advisedLineRef.current = chart.addSeries(LineSeries, {
      color: "#7aa2ff",
      lineWidth: 2,
      title: pair.advised,
      priceLineVisible: false,
    });
    baseLineRef.current = chart.addSeries(LineSeries, {
      color: "#82878f",
      lineWidth: 1,
      title: pair.base,
      priceLineVisible: false,
    });
    diffLineRef.current = chart.addSeries(LineSeries, {
      color: "#43b57a",
      lineWidth: 1,
      lineStyle: 2, // dashed
      title: "difference",
      priceLineVisible: false,
    });
    return () => {
      chart.remove();
      chartRef.current = null;
      advisedLineRef.current = null;
      baseLineRef.current = null;
      diffLineRef.current = null;
    };
    // Intentionally mount-once (pair.advised/pair.base name the lines' titles at creation time,
    // same as CumulativeCard's per-tag titles) -- a pair's own identity does not change across
    // polls, only its data does, which the effect below updates via setData().
  }, []);

  useEffect(() => {
    const advisedLine = advisedLineRef.current;
    const baseLine = baseLineRef.current;
    const diffLine = diffLineRef.current;
    if (advisedLine === null || baseLine === null || diffLine === null) return;
    const t = (session: string) => (Date.parse(session + "T00:00:00Z") / 1000) as UTCTimestamp;

    const cumulative = (sessionNets: Array<[string, number]>): Map<string, number> => {
      let cum = 0;
      const out = new Map<string, number>();
      for (const [session, net] of sessionNets) {
        cum += net;
        out.set(session, cum);
      }
      return out;
    };

    const advisedCum = cumulative(advisedGroup.sessionNets);
    const baseCum = cumulative(baseGroup.sessionNets);

    advisedLine.setData([...advisedCum].map(([session, v]) => ({ time: t(session), value: v })));
    baseLine.setData([...baseCum].map(([session, v]) => ({ time: t(session), value: v })));

    // Only sessions BOTH sides actually recorded -- a date only one side traded says nothing about
    // the comparison, matching pair.sessionsPaired's own definition.
    const sharedSessions = [...advisedCum.keys()].filter((s) => baseCum.has(s)).sort();
    diffLine.setData(sharedSessions.map((s) => ({ time: t(s), value: advisedCum.get(s)! - baseCum.get(s)! })));

    chartRef.current?.timeScale().fitContent();
  }, [advisedGroup, baseGroup]);

  return (
    <Card
      title={`${pair.advised} vs ${pair.base}`}
      collapseKey={`performance-ab-${pair.advised}`}
      controls={
        pair.underpowered === true ? (
          <span className="chip chip-warn" title="Below the promotion gate's sample and day thresholds">
            underpowered
          </span>
        ) : undefined
      }
    >
      <p className="muted" style={{ fontSize: 11, marginBottom: "0.4rem" }}>
        {pair.sessionsPaired} session{pair.sessionsPaired === 1 ? "" : "s"} paired
        {pair.experimentId !== null && <> · {pair.experimentId}</>}
      </p>
      {/* Always mounted, even at sessionsPaired=0 -- the chart mount effect runs once on mount
          (deps: []), so a host div that only appears once sessionsPaired later goes positive
          would leave the chart never created. The empty state is the message below the chart, not
          instead of it. */}
      {pair.sessionsPaired === 0 && <p className="muted">no sessions where both books traded yet</p>}
      <div ref={hostRef} style={{ height: "16rem", display: pair.sessionsPaired === 0 ? "none" : undefined }} />
    </Card>
  );
}
