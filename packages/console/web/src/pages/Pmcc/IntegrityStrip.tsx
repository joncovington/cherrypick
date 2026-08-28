import type { PmccPayload } from "@console/shared";
import { Card, fmtPct } from "../../components/DataTable";
import { MeasurementBreaks } from "../../components/MeasurementBreaks";

/**
 * Everything that bounds how far this page's net can be trusted, above the net rather than beneath
 * it.
 *
 * The module's second honesty rule is that early assignment is unmodelled but MEASURED, and that
 * the paper result is therefore an upper bound — "do not read the books' net as achievable live
 * until that exposure is read beside it". A caveat rendered under a P&L figure, or behind a
 * tooltip, is a caveat the reader meets after they have already formed the number's meaning. So it
 * leads.
 *
 * Amber throughout, never the cherry accent: that color is reserved for brand, live-mode and alert
 * moments, and none of these are alerts. A cold start is the honest state, not a fault.
 */
export function IntegrityStrip({ data, updatedAt }: { data: PmccPayload | undefined; updatedAt?: number }) {
  const integrity = data?.integrity;
  const exposure = integrity?.exposure;
  const exposedShare =
    exposure !== undefined && exposure.markedTicks > 0 ? (exposure.exposedTicks / exposure.markedTicks) * 100 : null;
  const dividendsDue = integrity?.dividends.filter((d) => d.refreshDue) ?? [];
  const coverage = integrity?.markCoverage;
  const drift = integrity?.schemaDrift ?? [];
  const breaks = integrity?.measurementBreaks ?? [];
  const settlementStyle = data?.params.settlementStyle ?? {};
  const symbols = data?.params.symbols ?? [];
  const physicalSymbols = symbols.filter((s) => settlementStyle[s] === "physical");
  const cashSymbols = symbols.filter((s) => settlementStyle[s] === "cash");

  return (
    <Card title="measurement integrity" collapseKey="pmcc-integrity" updatedAt={updatedAt} className="view-fade">
      <div className="pmcc-integrity">
        <div className="integrity-integrity-banner">
          <strong>Paper net is an upper bound{physicalSymbols.length > 0 ? "" : " where a symbol is physical-settlement"}.</strong>{" "}
          This module sells ITM calls by design, and on a physical-settlement symbol
          {physicalSymbols.length > 0 && <> ({physicalSymbols.join(", ")})</>} a short call with near-zero extrinsic
          can be assigned any day. Early assignment is <em>measured, never modelled</em>, for those symbols:{" "}
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
              carried exposed marks — {exposure.exposedTicks.toLocaleString()} of{" "}
              {exposure.markedTicks.toLocaleString()} usable short marks ({fmtPct(exposedShare, 1)}) sat under the
              flag. That share bounds what the unmodelled mechanism could have touched.
            </>
          )}
          {cashSymbols.length > 0 && (
            <>
              {" "}
              <span className="mono">{cashSymbols.join(", ")}</span> {cashSymbols.length === 1 ? "is" : "are"}{" "}
              European, cash-settled: no early-exercise risk exists, so the exposure telemetry above is exempt for
              {cashSymbols.length === 1 ? " it" : " them"} and its net carries no such upper-bound caveat.
            </>
          )}
        </div>

        <div className="integrity-integrity-grid">
          <section>
            <h3>dividend calendar</h3>
            {integrity === undefined || integrity.dividends.length === 0 ? (
              <p className="muted">no symbols declared</p>
            ) : (
              <ul className="integrity-plain-list">
                {integrity.dividends.map((d) => (
                  <li key={d.symbol}>
                    <span className="integrity-sym">{d.symbol}</span>{" "}
                    {d.declaredThrough === null ? (
                      <span className="integrity-warn" title="A span the calendar cannot answer for is refused outright (dividend_calendar_lapsed).">
                        undeclared — entries refused
                      </span>
                    ) : (
                      <>
                        <span className="muted">through</span> {d.declaredThrough}
                        {d.refreshDue && (
                          <span
                            className="chip chip-warn integrity-chip"
                            title="Hand-refreshed quarterly from the issuer's schedule. A span past this date refuses entry rather than assuming itself dividend-free, so a lapsed table halts entries loudly."
                          >
                            refresh due
                          </span>
                        )}
                        {d.exDates.length > 0 && (
                          <span className="muted"> · {d.exDates.length} ex-date{d.exDates.length === 1 ? "" : "s"}</span>
                        )}
                      </>
                    )}
                  </li>
                ))}
              </ul>
            )}
            {dividendsDue.length > 0 && (
              <p className="integrity-note">
                Refresh from the issuer's distribution schedule ({dividendsDue.map((d) => d.symbol).join(", ")})
                before the next entry spans it. Cash-settled symbols carry no ex-dividend check and never appear
                here — the check only runs for physical-settlement symbols.
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
                    {coverage.refused.toLocaleString()} refused ({fmtPct(
                      coverage.refusalShare === null ? null : coverage.refusalShare * 100,
                      1,
                    )})
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
                writer has moved on — this page may be reading a narrower story than it is recording.
              </p>
            )}
            <MeasurementBreaks breaks={breaks} />
          </div>
        )}
      </div>
    </Card>
  );
}
