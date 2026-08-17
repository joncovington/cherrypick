import type { PmccPayload } from "@console/shared";
import { Card, fmtPct } from "../../components/DataTable";

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
  const coldStart = integrity?.keltner.filter((k) => k.bars < k.required) ?? [];
  const coverage = integrity?.markCoverage;
  const drift = integrity?.schemaDrift ?? [];
  const breaks = integrity?.measurementBreaks ?? [];

  return (
    <Card title="measurement integrity" collapseKey="pmcc-integrity" updatedAt={updatedAt} className="view-fade">
      <div className="pmcc-integrity">
        <div className="pmcc-integrity-banner">
          <strong>Paper net is an upper bound.</strong> This module sells ITM calls by design, and a short call
          with near-zero extrinsic can be assigned any day. Early assignment is <em>measured, never modelled</em>:{" "}
          {exposure === undefined || exposure.markedTicks === 0 ? (
            <span className="muted">no usable short marks recorded yet, so there is nothing to bound.</span>
          ) : exposure.positionsWithExposure === 0 ? (
            <>
              no position has yet marked under the exposure threshold across{" "}
              {exposure.markedTicks.toLocaleString()} usable short marks.
            </>
          ) : (
            <>
              <span className="pmcc-warn">
                {exposure.positionsWithExposure} position{exposure.positionsWithExposure === 1 ? "" : "s"}
              </span>{" "}
              carried exposed marks — {exposure.exposedTicks.toLocaleString()} of{" "}
              {exposure.markedTicks.toLocaleString()} usable short marks ({fmtPct(exposedShare, 1)}) sat under the
              flag. That share bounds what the unmodelled mechanism could have touched.
            </>
          )}
        </div>

        <div className="pmcc-integrity-grid">
          <section>
            <h3>dividend calendar</h3>
            {integrity === undefined || integrity.dividends.length === 0 ? (
              <p className="muted">no symbols declared</p>
            ) : (
              <ul className="pmcc-plain-list">
                {integrity.dividends.map((d) => (
                  <li key={d.symbol}>
                    <span className="pmcc-sym">{d.symbol}</span>{" "}
                    {d.declaredThrough === null ? (
                      <span className="pmcc-warn" title="A span the calendar cannot answer for is refused outright (dividend_calendar_lapsed).">
                        undeclared — entries refused
                      </span>
                    ) : (
                      <>
                        <span className="muted">through</span> {d.declaredThrough}
                        {d.refreshDue && (
                          <span
                            className="chip chip-warn pmcc-chip"
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
              <p className="pmcc-note">
                Refresh from the issuer pages (Direxion for TNA, ProShares for TQQQ/UPRO) before the next entry
                spans it.
              </p>
            )}
          </section>

          <section>
            <h3>keltner readiness{coldStart.length > 0 && " — cold start"}</h3>
            {integrity === undefined || integrity.keltner.length === 0 ? (
              <p className="muted">no symbols declared</p>
            ) : (
              <div>
                {integrity.keltner.map((k) => {
                  const pct = k.required > 0 ? Math.min(100, (k.bars / k.required) * 100) : 0;
                  const ready = k.bars >= k.required;
                  // A finished cold start is not a progress bar. Once the channel exists the number
                  // that matters is that it does; a meter pinned at 100% reading "65/21" invites the
                  // reader to work out whether 65 of 21 is a problem.
                  return ready ? (
                    <div className="check-row" key={k.symbol}>
                      <span className="check-label">{k.symbol}</span>
                      <span className="chip chip-ok pmcc-chip">channel ready</span>
                      <span className="check-value muted">{k.bars} bars</span>
                    </div>
                  ) : (
                    <div className="check-row" key={k.symbol} title={`gating entries until ${String(k.required - k.bars)} more completed bars`}>
                      <span className="check-label">{k.symbol}</span>
                      <div className="check-track">
                        <div className="check-fill" style={{ width: `${String(pct)}%`, background: "var(--warn)" }} />
                      </div>
                      <span className="check-value">
                        {k.bars}/{k.required}
                      </span>
                    </div>
                  );
                })}
              </div>
            )}
            <p className="pmcc-note">
              {coldStart.length > 0
                ? "The refusal is the honest state, not a failure — the keltner book cannot enter until its channel exists."
                : "The channel exists for every symbol, so any keltner refusal from here is the filter's own verdict rather than missing history."}
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
                  <span className={coverage.refused > 0 ? "pmcc-warn" : ""}>
                    {coverage.refused.toLocaleString()} refused ({fmtPct(
                      coverage.refusalShare === null ? null : coverage.refusalShare * 100,
                      1,
                    )})
                  </span>
                </p>
                {coverage.refusals.length > 0 && (
                  <ul className="pmcc-plain-list">
                    {coverage.refusals.slice(0, 4).map((r) => (
                      <li key={r.reason}>
                        <span className="mono">{r.reason}</span> <span className="muted">× {r.n}</span>
                      </li>
                    ))}
                  </ul>
                )}
                <p className="pmcc-note">
                  A refused mark is still a row: a stalled feed and a quiet market must never look identical.
                </p>
              </>
            )}
          </section>
        </div>

        {(drift.length > 0 || breaks.length > 0) && (
          <div className="pmcc-integrity-alerts">
            {drift.length > 0 && (
              <p className="pmcc-err">
                <strong>Schema drift.</strong> The ledger holds {drift.length} column
                {drift.length === 1 ? "" : "s"} this console build does not know ({drift.join(", ")}). The module's
                writer has moved on — this page may be reading a narrower story than it is recording.
              </p>
            )}
            {breaks.length > 0 && (
              <div className="pmcc-err">
                <strong>Measurement breaks.</strong> Results either side of these dates must not be pooled.
                <ul className="pmcc-plain-list">
                  {breaks.map((b) => (
                    <li key={`${b.date}-${b.key}`}>
                      {b.date} · <span className="mono">{b.key}</span>
                      {b.note !== null && <span className="muted"> — {b.note}</span>}
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
