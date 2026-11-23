import type { BwbPayload } from "@console/shared";
import { Card } from "../../components/DataTable";
import { CoverageLine, IntegrityAlerts } from "../../components/IntegrityParts";

/**
 * Everything that bounds how far this page's numbers can be trusted, above them rather than
 * beneath -- the curve/pmcc/calendars precedent.
 *
 * bwb's own honesty rules 4 and 8: a trigger can only fire on a MEASURED tick (missing/stale
 * greeks or GEX inputs mean the trigger cannot evaluate that tick, never a guess), and measurement
 * breaks are journaled rows. This strip surfaces the trigger-tick and mark coverage for today's
 * session, plus schema drift and any measurement break on file.
 */
export function IntegrityStrip({ data, updatedAt }: { data: BwbPayload | undefined; updatedAt?: number }) {
  const integrity = data?.integrity;
  const triggerCoverage = integrity?.triggerCoverage;
  const markCoverage = integrity?.markCoverage;
  const drift = integrity?.schemaDrift ?? [];
  const breaks = integrity?.measurementBreaks ?? [];

  return (
    <Card title="measurement integrity" collapseKey="bwb-integrity" updatedAt={updatedAt} className="view-fade">
      <div className="pmcc-integrity">
        <div className="integrity-integrity-grid">
          <section>
            <h3>trigger-tick coverage</h3>
            {triggerCoverage === undefined || triggerCoverage.ticks === 0 ? (
              <p className="muted">no trigger ticks recorded on this session yet</p>
            ) : (
              <CoverageLine count={triggerCoverage.ticks} noun="ticks" refused={triggerCoverage.refused} share={triggerCoverage.refusalShare} refusedLabel="unmeasured" />
            )}
            {triggerCoverage !== undefined && triggerCoverage.totalFailure && (
              <p className="integrity-warn">
                <strong>Every tick this session was unmeasured.</strong> The loop ran and each read
                failed -- that is a defect, not thin data, and this session is unusable as replay
                substrate.
              </p>
            )}
            {triggerCoverage !== undefined && triggerCoverage.refused > 0 && (
              <p className="muted">
                {triggerCoverage.noSpot.toLocaleString()} without spot ·{" "}
                {triggerCoverage.noFlip.toLocaleString()} without gamma flip
                {Object.keys(triggerCoverage.reasons).length > 0 && (
                  <>
                    {" — "}
                    {Object.entries(triggerCoverage.reasons)
                      .map(([reason, n]) => `${reason} (${n})`)
                      .join("; ")}
                  </>
                )}
              </p>
            )}
            <p className="integrity-note">
              A trigger can only fire on a measured tick -- missing/stale greeks or GEX inputs mean the
              trigger cannot evaluate that tick, never a guess. The two halves are reported
              separately: a tick with spot and no flip is a different outage from one with neither,
              and a single share cannot say which.
            </p>
          </section>

          <section>
            <h3>mark coverage</h3>
            {markCoverage === undefined || markCoverage.marks === 0 ? (
              <p className="muted">no marks on this session</p>
            ) : (
              <CoverageLine count={markCoverage.marks} noun="marks" refused={markCoverage.refused} share={markCoverage.refusalShare} />
            )}
            <p className="integrity-note">
              A refused mark is still a row: a stalled feed and a quiet market must never look identical.
            </p>
          </section>
        </div>

        <IntegrityAlerts drift={drift} breaks={breaks} />
      </div>
    </Card>
  );
}
