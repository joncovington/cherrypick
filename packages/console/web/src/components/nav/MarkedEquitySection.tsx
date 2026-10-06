import type { ReactNode } from "react";
import type { EquityPayload } from "@console/shared";
import { Card } from "../DataTable";
import { TimeLineChart } from "../chart/TimeLineChart";
import { SERIES_COLORS } from "../Charts";
import { FIXED_STRESS_WINDOWS, inversionWindows } from "../../lib/navSeries";
import { EquityTiles, MonthlyHeatmap, StressTable, UnderwaterChart, type NamedSeries } from "./NavViews";

/**
 * A module's daily MARKED equity by arm (`<module> equity`, core.metrics.nav): closed nets on their
 * close session plus every open position at that session's mark, in dollars from zero after every
 * cost. Shared by curve (2026-10-05) and pmcc (2026-10-06): a position held for weeks or a year can
 * sit far under water and still close a win, and the closed-trade view records only the win.
 *
 * `regimeRows` (optional) adds the live inversion windows from a module's own VIX/VIX3M series to
 * the fixed short-vol stress episodes.
 */
export function MarkedEquitySection({
  equity,
  loading,
  regimeRows,
  note,
  collapsePrefix,
}: {
  equity: EquityPayload | undefined;
  loading: boolean;
  regimeRows?: Array<{ tradeDate: string; ratio: number | null }>;
  /** What "marked" means for this module, one or two sentences. */
  note: ReactNode;
  collapsePrefix: string;
}) {
  const arms = Object.entries(equity?.arms ?? {});
  const named: NamedSeries[] = arms.map(([arm, e], i) => ({
    label: arm,
    data: e.series,
    color: SERIES_COLORS[i % SERIES_COLORS.length],
  }));
  const windows = [...FIXED_STRESS_WINDOWS, ...inversionWindows(regimeRows ?? [])];
  const failed = equity !== undefined && !equity.ok;
  return (
    <>
      <Card title="marked equity by arm" collapseKey={`${collapsePrefix}-equity`} isError={failed}>
        {failed ? (
          <p className="integrity-warn">{equity.error}</p>
        ) : named.every((n) => n.data.length < 2) ? (
          <p className="muted">{loading ? "reading…" : "not enough sessions yet"}</p>
        ) : (
          <TimeLineChart
            series={named.map((n) => ({ label: n.label, color: n.color, points: n.data.map(([x, y]) => ({ x, y })) }))}
            height={240}
          />
        )}
        <p className="integrity-note">{note}</p>
      </Card>
      <Card title="below the high" collapseKey={`${collapsePrefix}-underwater`}>
        <UnderwaterChart series={named} kind="equity" />
      </Card>
      {arms.map(([arm, e]) => (
        <Card key={arm} title={`${arm} -- marked over ${String(e.reading.days)} sessions`} collapseKey={`${collapsePrefix}-${arm}`} defaultCollapsed={arms.length > 4}>
          <EquityTiles reading={e.reading} />
          {e.carried > 0 && (
            <p className="integrity-note">
              {e.carried} position-session mark{e.carried === 1 ? "" : "s"} carried forward (unpriceable that session).
            </p>
          )}
          <MonthlyHeatmap monthly={e.reading.monthly} kind="equity" />
        </Card>
      ))}
      <Card title="stress windows" collapseKey={`${collapsePrefix}-stress`}>
        <StressTable series={named} windows={windows} kind="equity" />
        <p className="integrity-note">
          The fixed short-vol episodes predate this module and read "not held"
          {regimeRows !== undefined ? "; the live rows are every stretch its own regime read sat at or above 1.0" : ""}.
        </p>
      </Card>
    </>
  );
}
