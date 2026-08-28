import type { CalendarsPayload, CalendarsPoliciesPayload } from "@console/shared";
import { Card, fmtPct } from "../../components/DataTable";
import { MeasurementBreaks } from "../../components/MeasurementBreaks";

/**
 * Everything that bounds how far this page's numbers can be trusted, above them rather than beneath.
 *
 * This module's whole output is a ranking of exit rules, and its seventh honesty rule is that the
 * ranking never travels without the reason to believe it. So the validation leads — not as a badge
 * on the policy table, where a reader meets it after they have already picked a winner, but here,
 * before any number.
 *
 * The rest of the strip is the substrate the ranking rests on: the tick cadence that bounds how
 * precisely a trigger can replay, the mark coverage that says whether a barren week was a quiet
 * market or a thin feed, the dividend table whose lapse stops entries by design, and the delivered
 * shares that make a `path` week's drawdown unbounded by its debit.
 *
 * Amber throughout, never the cherry accent: that is reserved for brand, live-mode and alerts, and
 * none of these are alerts. A skipped ex-dividend week is the design, not a fault.
 */
export function IntegrityStrip({
  data,
  policies,
  updatedAt,
}: {
  data: CalendarsPayload | undefined;
  policies: CalendarsPoliciesPayload | undefined;
  updatedAt?: number;
}) {
  const integrity = data?.integrity;
  const coverage = integrity?.markCoverage;
  const drift = integrity?.schemaDrift ?? [];
  const breaks = integrity?.measurementBreaks ?? [];
  const cadence = integrity?.tickCadence;
  const validation = policies?.validation;
  const dividendsDue = integrity?.dividends.filter((d) => d.refreshDue) ?? [];
  const undeclared = integrity?.settlement.filter((s) => s.style === null) ?? [];

  return (
    <Card title="measurement integrity" collapseKey="cal-integrity" updatedAt={updatedAt} className="view-fade">
      <div className="cal-integrity">
        <div className="integrity-integrity-banner">
          <strong>The policy table is only as good as its validation.</strong> The derivation re-runs the{" "}
          <span className="mono">control</span> policy over the control book&rsquo;s own marks and the{" "}
          <span className="mono">expiry-longs-mon</span> policy over the path book&rsquo;s, and both must
          reproduce those books&rsquo; real recorded nets to the cent.{" "}
          {policies === undefined ? (
            <span className="muted">Not read yet.</span>
          ) : policies.error !== null ? (
            <span className="integrity-err">The derivation could not be run — {policies.error}</span>
          ) : validation == null || validation.compared === 0 ? (
            <span className="muted">
              No completed week has been derived yet, so there is nothing to check and nothing to rank.
            </span>
          ) : validation.ok ? (
            <>
              <span className="cal-ok">
                {validation.compared} week{validation.compared === 1 ? "" : "s"} reproduced
              </span>{" "}
              — the replay and the books agree about the same trades.
            </>
          ) : (
            <span className="integrity-err">
              {validation.mismatches.length} of {validation.compared} checked week
              {validation.compared === 1 ? "" : "s"} disagree with the books. Do not read the ranking until
              that is explained.
            </span>
          )}
        </div>

        {validation != null && validation.mismatches.length > 0 && (
          <ul className="integrity-plain-list integrity-err">
            {validation.mismatches.map((m) => (
              <li key={`${m.weekOf}-${m.book}`}>
                <span className="mono">{m.weekOf}</span> · {m.book} —{" "}
                {m.reason !== null
                  ? `not derivable (${m.reason})`
                  : `derived ${String(m.derivedNet)} vs recorded ${String(m.realNet)} (diff ${String(m.diff)})`}
              </li>
            ))}
          </ul>
        )}

        <div className="integrity-integrity-grid">
          <section>
            <h3>tick cadence</h3>
            {cadence === null || cadence === undefined ? (
              <p className="muted">not recorded</p>
            ) : (
              <p>
                marks every <strong>{cadence.seconds ?? "—"}s</strong>
                {cadence.since !== null && <span className="muted"> since {cadence.since}</span>}
              </p>
            )}
            <p className="integrity-note">
              A trigger is evaluated at the recorded cadence, so a threshold crossed and re-crossed between
              ticks is invisible — the derived exit is the first <em>recorded</em> tick where it held. Changing
              the cadence is a journaled measurement break, and derivations never pool across one.
            </p>
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
              </>
            )}
            <p className="integrity-note">
              A refused mark is still a row: a stalled feed and a quiet market must never look identical, and
              a hole in the path makes a policy <span className="mono">derivable: false</span>, never zero.
            </p>
          </section>

          <section>
            <h3>settlement &amp; dividends</h3>
            {integrity === undefined || integrity.settlement.length === 0 ? (
              <p className="muted">no symbols declared</p>
            ) : (
              <ul className="integrity-plain-list">
                {integrity.settlement.map((s) => {
                  const div = integrity.dividends.find((d) => d.symbol === s.symbol);
                  return (
                    <li key={s.symbol}>
                      <span className="integrity-sym">{s.symbol}</span>{" "}
                      {s.style === null ? (
                        <span
                          className="integrity-warn"
                          title="A symbol declared as neither cash nor physical is refused at entry (unknown_settlement) rather than assumed into a style."
                        >
                          settlement undeclared — entries refused
                        </span>
                      ) : (
                        <>
                          <span className="chip integrity-chip-quiet integrity-chip">{s.style}</span>
                          {div !== undefined && div.declaredThrough !== null && (
                            <span className="muted">
                              {" "}
                              · ex-div declared through {div.declaredThrough} ({div.exDates.length} date
                              {div.exDates.length === 1 ? "" : "s"})
                            </span>
                          )}
                          {div !== undefined && div.refreshDue && (
                            <span
                              className="chip chip-warn integrity-chip"
                              title="Refreshed annually by hand from the issuer's own distribution schedule. A week past this date refuses entry (dividend_calendar_lapsed) rather than assuming itself dividend-free."
                            >
                              refresh due
                            </span>
                          )}
                        </>
                      )}
                    </li>
                  );
                })}
              </ul>
            )}
            <p className="integrity-note">
              Ex-dividend weeks are <em>skipped, not modelled</em> — roughly four a year, and they are exactly
              the quarterly-expiration weeks. The pooled table therefore covers ordinary weeks only.
            </p>
            {(dividendsDue.length > 0 || undeclared.length > 0) && (
              <p className="integrity-note integrity-warn">
                Refresh the block from the issuer&rsquo;s distribution schedule before the next entry spans it.
              </p>
            )}
          </section>

          <section>
            <h3>delivered shares</h3>
            {integrity === undefined || integrity.openShareAssignments === 0 ? (
              <p className="muted">none outstanding</p>
            ) : (
              <p className="integrity-warn">
                {integrity.openShareAssignments} share position
                {integrity.openShareAssignments === 1 ? "" : "s"} held, awaiting disposal
              </p>
            )}
            <p className="integrity-note">
              Under physical settlement an ITM short hands over shares that ride to the next session — over
              the <em>weekend</em>, for a Friday short. A week does not close while its shares are
              outstanding, and a <span className="mono">path</span> or <span className="mono">expiry-*</span>{" "}
              drawdown is <strong>not</strong> bounded by the entry debit the way its capital figure is.
            </p>
          </section>
        </div>

        {(drift.length > 0 || breaks.length > 0) && (
          <div className="integrity-integrity-alerts">
            {drift.length > 0 && (
              <p className="integrity-err">
                <strong>Schema drift.</strong> The ledger holds {drift.length} column
                {drift.length === 1 ? "" : "s"} this console build does not know ({drift.join(", ")}). The
                module&rsquo;s writer has moved on — this page may be reading a narrower story than it is
                recording.
              </p>
            )}
            <MeasurementBreaks breaks={breaks} />
          </div>
        )}
      </div>
    </Card>
  );
}
