import type { CurvePayload } from "@console/shared";
import { Card } from "../../components/DataTable";
import { TimeLineChart } from "../../components/chart/TimeLineChart";
import { SERIES_COLORS } from "../../components/Charts";
import { PerformanceSlide } from "../../components/performance/PerformanceSlide";
import {
  EquityTiles,
  MonthlyHeatmap,
  StressTable,
  UnderwaterChart,
  type NamedSeries,
} from "../../components/nav/NavViews";
import { useCurveEquity } from "../../lib/api";
import { FIXED_STRESS_WINDOWS, inversionWindows } from "../../lib/navSeries";

/**
 * curve's performance page. It leads with the daily MARKED equity (`curve equity`,
 * core.metrics.nav): closed nets on their close session plus every open spread at that session's
 * mark. A credit spread can sit deep under water for weeks and close a small win; the closed-trade
 * view below records the win and nothing else, so it comes second.
 */
export function CurvePerformance({ data }: { data: CurvePayload | undefined }) {
  const equity = useCurveEquity();
  const arms = Object.entries(equity.data?.arms ?? {});
  const named: NamedSeries[] = arms.map(([arm, e], i) => ({
    label: arm,
    data: e.series,
    color: SERIES_COLORS[i % SERIES_COLORS.length],
  }));
  const windows = [
    ...FIXED_STRESS_WINDOWS,
    ...inversionWindows((data?.regimeSeries ?? []).filter((r) => r.usable)),
  ];
  return (
    <div className="cards cards-wide">
      <Card title="marked equity by arm" collapseKey="curve-perf-equity" isError={equity.data !== undefined && !equity.data.ok}>
        {equity.data !== undefined && !equity.data.ok ? (
          <p className="integrity-warn">{equity.data.error}</p>
        ) : named.every((n) => n.data.length < 2) ? (
          <p className="muted">{equity.isLoading ? "reading…" : "not enough sessions yet"}</p>
        ) : (
          <TimeLineChart
            series={named.map((n) => ({ label: n.label, color: n.color, points: n.data.map(([x, y]) => ({ x, y })) }))}
            height={240}
          />
        )}
        <p className="integrity-note">
          Dollars from zero, after every cost: each closed cycle's net on its close session, each open spread at that
          session's last usable mark less its entry costs. A session with no usable mark carries the last one and is
          counted below.
        </p>
      </Card>
      <Card title="below the high" collapseKey="curve-perf-underwater">
        <UnderwaterChart series={named} kind="equity" />
      </Card>
      {arms.map(([arm, e]) => (
        <Card key={arm} title={`${arm} -- marked over ${String(e.reading.days)} sessions`} collapseKey={`curve-perf-${arm}`}>
          <EquityTiles reading={e.reading} />
          {e.carried > 0 && (
            <p className="integrity-note">
              {e.carried} session mark{e.carried === 1 ? "" : "s"} carried forward (no usable mark that session).
            </p>
          )}
          <MonthlyHeatmap monthly={e.reading.monthly} kind="equity" />
        </Card>
      ))}
      <Card title="stress windows" collapseKey="curve-perf-stress">
        <StressTable series={named} windows={windows} kind="equity" />
        <p className="integrity-note">
          The fixed short-vol episodes predate this module and read "not held"; the live rows are every stretch its own
          regime read sat at or above 1.0.
        </p>
      </Card>
      <PerformanceSlide module="curve" />
    </div>
  );
}
