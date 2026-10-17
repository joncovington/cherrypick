import { Card, SortTh, sortRows, useSort } from "../DataTable";
import { fmtMoney } from "../../lib/format";
import type { ExitReasonRow, TradingMode } from "@console/shared";
import { useModulePerformance, type PerformanceModuleId } from "../../lib/api";
import { matchesArm } from "../../lib/armTag";
import { MetricTiles } from "./MetricTiles";
import { ExcursionsCard } from "./ExcursionsCard";
import { CumulativeCard } from "./CumulativeCard";
import { PairedABCard } from "./PairedABCard";
import { TradeNetHistogram } from "./TradeNetHistogram";

/** The exit-reason tally, sortable once the reader's rows are known to be rows (not `unavailable`). */
function ExitReasonsCard({ module, rows }: { module: PerformanceModuleId; rows: ExitReasonRow[] }) {
  const sort = useSort();
  const sorted = sortRows(rows, sort, {
    tag: (r) => r.tag,
    reason: (r) => r.reason,
    n: (r) => r.n,
    net: (r) => r.net,
  });
  return (
    <Card title="exit reasons" collapseKey={`performance-${module}-exits`} defaultCollapsed>
      <div className="table-scroll">
      <table className="data-table">
        <thead>
          <tr>
            <SortTh label="tag" k="tag" sort={sort} />
            <SortTh label="reason" k="reason" sort={sort} />
            <SortTh label="n" k="n" sort={sort} />
            <SortTh label="net" k="net" sort={sort} />
          </tr>
        </thead>
        <tbody>
          {sorted.map((r) => (
            <tr key={`${r.tag}-${r.reason}`}>
              <td>{r.tag}</td>
              <td>{r.reason}</td>
              <td>{r.n}</td>
              <td className={r.net === null ? "muted" : r.net >= 0 ? "pnl-pos" : "pnl-neg"}>
                {fmtMoney(r.net)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      </div>
    </Card>
  );
}

/**
 * The suite-wide performance slide: one calibration reading per arm, via
 * `GET /api/performance/:module` (`server/src/readers/performance.ts`). Generic over every module
 * this reads (curve/pmcc/calendars/bwb/meic/flies/earnings all share ONE schema registry and ONE
 * metric vocabulary -- `core.metrics.calibration_reading`), so this component is not written per
 * module the way each module's OWN richer tab is.
 *
 * A module's `underpowered` verdict rides on its `pairs` entry (the experiment that produced the
 * `advised:` book), not on the base group's own tile row -- a base was never an experiment and
 * has no verdict to carry. Several pairs can share one base (2026-09-17: each experiment has its
 * own book), so the base's chip says "every paired experiment" only when all of them are below
 * the gate.
 */
export function PerformanceSlide({
  module,
  mode = "paper",
  arm = null,
}: {
  module: PerformanceModuleId;
  mode?: TradingMode;
  /** The page's arm picker (flies, MEIC): narrows the arm cards, pairs and exit reasons to that arm.
   *  The cumulative chart keeps `control` beside it as the reference. null is every arm. */
  arm?: string | null;
}) {
  const { data, isLoading, dataUpdatedAt } = useModulePerformance(module, "current", mode);

  if (isLoading || data === undefined) {
    return (
      <div className="cards cards-wide">
        <Card title="performance">
          <span className="skeleton skeleton-text" style={{ width: "50%" }} />
        </Card>
      </div>
    );
  }

  if (!data.ok) {
    return (
      <div className="cards cards-wide">
        <Card title="performance" isError>
          <p className="muted">{data.error ?? "calibration reading unavailable"}</p>
        </Card>
      </div>
    );
  }

  // Per base: true only when every experiment paired against it with a verdict is underpowered.
  const underpoweredByBase = new Map<string, boolean>();
  for (const p of data.pairs) {
    if (p.underpowered === null) continue;
    underpoweredByBase.set(p.base, (underpoweredByBase.get(p.base) ?? true) && p.underpowered);
  }
  const groupByTag = new Map(data.groups.map((g) => [g.tag, g]));
  // The arm picker, applied to what was read. Control stays on the cumulative chart as the line the
  // picked arm is read against; it is drawn there only, not given a card of its own.
  const shown = data.groups.filter((g) => matchesArm(g.tag, arm));
  const control = arm !== null && arm !== "control" ? groupByTag.get("control") : undefined;
  const charted = control !== undefined ? [...shown, control] : shown;

  return (
    <div className="cards cards-wide">
      {data.mode === "live" && (
        <p className="muted" style={{ fontSize: 12 }}>
          the <strong>live</strong> ledger — a measurement of the live book, not a promotion reading (those are
          paper only, and so are advised A/B pairs)
        </p>
      )}
      {data.era.note !== null && (
        <p className="muted" style={{ fontSize: 12 }}>
          scoped to the suite's current evidence window ({data.era.from ?? "all time"}) — {data.era.note}
          {data.era.moduleEra !== null && (
            <>
              {" "}· and to this module's own <strong>{data.era.moduleEra}</strong> era, by the era stamped on each
              position; earlier eras are in the history tab
            </>
          )}
        </p>
      )}
      {arm !== null && (
        <p className="muted" style={{ fontSize: 12 }}>
          showing <strong>{arm}</strong>, picked in the header
          {control !== undefined && "; control is drawn on the cumulative chart as the reference"}
        </p>
      )}
      {shown.length === 0 ? (
        <Card title="performance" updatedAt={dataUpdatedAt}>
          <p className="muted">
            {arm !== null && data.groups.length > 0 ? `no closed trades for ${arm} in this window` : "no closed trades in this window yet"}
            {data.era.moduleEra !== null && <> (the {data.era.moduleEra} era)</>}
          </p>
        </Card>
      ) : (
        <>
          <Card title="cumulative net P&L" collapseKey={`performance-${module}-cumulative`} updatedAt={dataUpdatedAt}>
            <CumulativeCard groups={charted} breaks={data.breaks} />
          </Card>
          {shown.map((g) => (
            <Card
              key={g.tag}
              title={g.tag}
              collapseKey={`performance-${module}-${g.tag}`}
              updatedAt={dataUpdatedAt}
            >
              <MetricTiles reading={g.reading} peakRisk={g.peakRisk} />
              <TradeNetHistogram nets={g.tradeNets} />
              {underpoweredByBase.get(g.tag) === true && (
                <p className="muted" style={{ fontSize: 11, marginTop: "0.4rem" }}>
                  <span className="chip chip-warn">underpowered</span> every experiment paired against this
                  book is still below the promotion gate's sample/day thresholds
                </p>
              )}
            </Card>
          ))}
        </>
      )}
      {Array.isArray(data.exitReasons) &&
        data.exitReasons.some((r) => matchesArm(r.tag, arm)) && (
          <ExitReasonsCard module={module} rows={data.exitReasons.filter((r) => matchesArm(r.tag, arm))} />
        )}
      {data.pairs.filter((p) => matchesArm(p.base, arm) || matchesArm(p.advised, arm)).map((p) => {
        const advisedGroup = groupByTag.get(p.advised);
        const baseGroup = groupByTag.get(p.base);
        // Both sides must actually be present as performance groups in THIS window -- a pair the
        // reader found (advisor.db still remembers the experiment) but whose books have no data
        // in the current era is not something a cumulative chart can show.
        if (advisedGroup === undefined || baseGroup === undefined) return null;
        return <PairedABCard key={p.advised} pair={p} advisedGroup={advisedGroup} baseGroup={baseGroup} />;
      })}
      {data.excursions.ok && <ExcursionsCard excursions={data.excursions} />}
      {data.breaks.length > 0 && (
        <Card title="measurement breaks" collapseKey={`performance-${module}-breaks`} defaultCollapsed>
          {data.breaks.map((b, i) => (
            <p key={i} className="muted" style={{ fontSize: 12, margin: "0.3rem 0" }}>
              <strong>{b.date}</strong> · {b.key}
              {b.scope !== null && ` (${b.scope})`}
              {b.note !== null && ` — ${b.note}`}
            </p>
          ))}
        </Card>
      )}
    </div>
  );
}
