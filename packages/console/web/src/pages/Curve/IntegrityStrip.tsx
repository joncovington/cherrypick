import type { CurvePayload } from "@console/shared";
import { Card, fmtPct } from "../../components/DataTable";

/**
 * Everything that bounds how far this page's net can be trusted, above the net rather than beneath
 * it -- the pmcc/calendars precedent.
 *
 * curve's honesty rule 2 states it plainly: early assignment is unmodelled but MEASURED, and the
 * paper result is therefore an upper bound. Rule 7 states the regime series must be written every
 * session, whether or not any book trades -- so this strip also answers "is today's regime row
 * present and usable", the continuity check the series exists for.
 */
export function IntegrityStrip({ data, updatedAt }: { data: CurvePayload | undefined; updatedAt?: number }) {
  const integrity = data?.integrity;
  const exposure = integrity?.exposure;
  const exposedShare =
    exposure !== undefined && exposure.markedTicks > 0 ? (exposure.exposedTicks / exposure.markedTicks) * 100 : null;
  const coverage = integrity?.markCoverage;
  const drift = integrity?.schemaDrift ?? [];
  const breaks = integrity?.measurementBreaks ?? [];
  const regimeToday = integrity?.regimeToday;

  return (
    <Card title="measurement integrity" collapseKey="curve-integrity" updatedAt={updatedAt} className="view-fade">
      <div className="pmcc-integrity">
        <div className="integrity-integrity-banner">
          <strong>Paper net is an upper bound.</strong> VXX pays no dividend, but a spike still puts the short call
          ITM. Early assignment is <em>measured, never modelled</em>:{" "}
          {exposure === undefined || exposure.markedTicks === 0 ? (
            <span className="muted">no usable short marks recorded yet, so there is nothing to bound.</span>
          ) : exposure.positionsWithExposure === 0 ? (
            <>
              no position has yet marked under the exposure threshold across{" "}
              {exposure.markedTicks.toLocaleString()} usable short marks.
            </>
          ) : (
            <>
              <span className="integrity-warn">
                {exposure.positionsWithExposure} position{exposure.positionsWithExposure === 1 ? "" : "s"}
              </span>{" "}
              carried exposed marks -- {exposure.exposedTicks.toLocaleString()} of{" "}
              {exposure.markedTicks.toLocaleString()} usable short marks ({fmtPct(exposedShare, 1)}) sat under the
              flag. That share bounds what the unmodelled mechanism could have touched.
            </>
          )}
        </div>

        <div className="integrity-integrity-grid">
          <section>
            <h3>regime series continuity</h3>
            {regimeToday === undefined || !regimeToday.present ? (
              <p className="integrity-warn">no regime row for the current session -- the series' value is its continuity, so a gap here matters even on a day nothing traded</p>
            ) : regimeToday.usable ? (
              <p className="muted">today's row is present and usable</p>
            ) : (
              <p className="integrity-warn">
                today's row is present but unusable{regimeToday.refusal !== null && <> ({regimeToday.refusal})</>} --
                a stale or missing quote refuses rather than freezing the last value forward
              </p>
            )}
          </section>

          <section>
            <h3>mark coverage</h3>
            {coverage === undefined || coverage.marks === 0 ? (
              <p className="muted">no marks on this session</p>
            ) : (
              <>
                <p>
                  {coverage.marks.toLocaleString()} marks ·{" "}
                  <span className={coverage.refused > 0 ? "integrity-warn" : ""}>
                    {coverage.refused.toLocaleString()} refused (
                    {fmtPct(coverage.refusalShare === null ? null : coverage.refusalShare * 100, 1)})
                  </span>
                </p>
                {coverage.refusals.length > 0 && (
                  <ul className="integrity-plain-list">
                    {coverage.refusals.slice(0, 4).map((r) => (
                      <li key={r.reason}>
                        <span className="mono">{r.reason}</span> <span className="muted">× {r.n}</span>
                      </li>
                    ))}
                  </ul>
                )}
                <p className="integrity-note">
                  A refused mark is still a row: a stalled feed and a quiet market must never look identical.
                </p>
              </>
            )}
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
