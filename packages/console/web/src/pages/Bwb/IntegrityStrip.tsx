import type { BwbPayload } from "@console/shared";
import { Card, fmtPct } from "../../components/DataTable";

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
              <p>
                {triggerCoverage.ticks.toLocaleString()} ticks ·{" "}
                <span className={triggerCoverage.refused > 0 ? "integrity-warn" : ""}>
                  {triggerCoverage.refused.toLocaleString()} unmeasured (
                  {fmtPct(triggerCoverage.refusalShare === null ? null : triggerCoverage.refusalShare * 100, 1)})
                </span>
              </p>
            )}
            <p className="integrity-note">
              A trigger can only fire on a measured tick -- missing/stale greeks or GEX inputs mean the
              trigger cannot evaluate that tick, never a guess.
            </p>
          </section>

          <section>
            <h3>mark coverage</h3>
            {markCoverage === undefined || markCoverage.marks === 0 ? (
              <p className="muted">no marks on this session</p>
            ) : (
              <p>
                {markCoverage.marks.toLocaleString()} marks ·{" "}
                <span className={markCoverage.refused > 0 ? "integrity-warn" : ""}>
                  {markCoverage.refused.toLocaleString()} refused (
                  {fmtPct(markCoverage.refusalShare === null ? null : markCoverage.refusalShare * 100, 1)})
                </span>
              </p>
            )}
            <p className="integrity-note">
              A refused mark is still a row: a stalled feed and a quiet market must never look identical.
            </p>
          </section>
        </div>

        {(drift.length > 0 || breaks.length > 0) && (
          <div className="integrity-integrity-alerts">
            {drift.length > 0 && (
              <p className="integrity-err">
                <strong>Schema drift.</strong> The ledger holds {drift.length} column
                {drift.length === 1 ? "" : "s"} this console build does not know ({drift.join(", ")}). The module's
                writer has moved on -- this page may be reading a narrower story than it is recording.
              </p>
            )}
            {breaks.length > 0 && (
              <div className="integrity-err">
                <strong>Measurement breaks.</strong> Results either side of these dates must not be pooled.
                <ul className="integrity-plain-list">
                  {breaks.map((b) => (
                    <li key={`${b.date}-${b.key}`}>
                      {b.date} · <span className="mono">{b.key}</span>
                      {b.note !== null && <span className="muted"> -- {b.note}</span>}
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </div>
        )}
      </div>
    </Card>
  );
}
