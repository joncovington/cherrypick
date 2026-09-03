import { Card } from "../DataTable";
import { useModulePerformance, type PerformanceModuleId } from "../../lib/api";
import { MetricTiles } from "./MetricTiles";
import { ExcursionsCard } from "./ExcursionsCard";

/**
 * The suite-wide performance slide: one calibration reading per profile, via
 * `GET /api/performance/:module` (`server/src/readers/performance.ts`). Generic over every module
 * this reads (curve/pmcc/calendars/bwb/meic/flies/earnings all share ONE schema registry and ONE
 * metric vocabulary -- `core.metrics.calibration_reading`), so this component is not written per
 * module the way each module's OWN richer tab is.
 *
 * A module's `underpowered` verdict rides on its `pairs` entry (the experiment that produced the
 * `advised:` twin), not on the control group's own tile row -- a control was never an experiment
 * and has no verdict to carry.
 */
export function PerformanceSlide({ module }: { module: PerformanceModuleId }) {
  const { data, isLoading, dataUpdatedAt } = useModulePerformance(module, "current");

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

  const underpoweredByBase = new Map(data.pairs.map((p) => [p.base, p.underpowered]));

  return (
    <div className="cards cards-wide">
      {data.era.note !== null && (
        <p className="muted" style={{ fontSize: 12 }}>
          scoped to the suite's current evidence window ({data.era.from ?? "all time"}) — {data.era.note}
        </p>
      )}
      {data.groups.length === 0 ? (
        <Card title="performance" updatedAt={dataUpdatedAt}>
          <p className="muted">no closed trades in this window yet</p>
        </Card>
      ) : (
        data.groups.map((g) => (
          <Card
            key={g.tag}
            title={g.tag}
            collapseKey={`performance-${module}-${g.tag}`}
            updatedAt={dataUpdatedAt}
          >
            <MetricTiles reading={g.reading} />
            {underpoweredByBase.get(g.tag) === true && (
              <p className="muted" style={{ fontSize: 11, marginTop: "0.4rem" }}>
                <span className="chip chip-warn">underpowered</span> the paired advised experiment
                has not yet reached the promotion gate's sample/day thresholds
              </p>
            )}
          </Card>
        ))
      )}
      {Array.isArray(data.exitReasons) && data.exitReasons.length > 0 && (
        <Card title="exit reasons" collapseKey={`performance-${module}-exits`} defaultCollapsed>
          <table className="data-table">
            <thead>
              <tr>
                <th>tag</th>
                <th>reason</th>
                <th>n</th>
                <th>net</th>
              </tr>
            </thead>
            <tbody>
              {data.exitReasons.map((r, i) => (
                <tr key={i}>
                  <td>{r.tag}</td>
                  <td>{r.reason}</td>
                  <td>{r.n}</td>
                  <td className={r.net === null ? "muted" : r.net >= 0 ? "pnl-pos" : "pnl-neg"}>
                    {r.net === null ? "—" : `$${r.net.toFixed(2)}`}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      )}
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
