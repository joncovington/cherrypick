import type { LiveMeicReadiness } from "@console/shared";

/**
 * Whether MEIC's live loop may be armed, as the module answers it (`arm_verdict`, bridged by the
 * server). The card draws the verdict, the held ICs and the loop's CRITICAL lines as given; it
 * computes nothing, and it has no button: arming is the human's `/live-meic-start`.
 *
 * Three states: ready, not ready (each blocker listed), and a bridge that failed -- which says so
 * and is never drawn as ready.
 */
export function MeicReadinessCard({ readiness }: { readiness: LiveMeicReadiness | undefined }) {
  if (readiness === undefined || !readiness.ok) {
    return (
      <section className="card">
        <h3>Live readiness</h3>
        <p className="pnl-neg">
          <strong>Readiness unknown</strong> — the status read failed, so nothing here says live is ready
        </p>
        <p className="muted">{readiness === undefined ? "the server sent no readiness block" : readiness.error}</p>
      </section>
    );
  }
  const newestFirst = [...readiness.criticals].reverse();
  return (
    <section className="card">
      <h3>Live readiness</h3>
      <p className={readiness.ready ? "pnl-pos" : "pnl-neg"}>
        <strong>{readiness.ready ? "Ready to arm" : "Not ready"}</strong>
      </p>
      {readiness.blockers.length > 0 && (
        <ul aria-label="blockers">
          {readiness.blockers.map((b) => (
            <li key={b} className="pnl-neg">
              {b}
            </li>
          ))}
        </ul>
      )}
      {readiness.warnings.length > 0 && (
        <ul aria-label="warnings">
          {readiness.warnings.map((w) => (
            <li key={w} className="muted">
              warning: {w}
            </li>
          ))}
        </ul>
      )}
      <h4>Held now</h4>
      {readiness.openIcs.length === 0 ? (
        <p className="muted">no iron condor held</p>
      ) : (
        <ul aria-label="held iron condors">
          {readiness.openIcs.map((ic) => (
            <li key={ic}>{ic}</li>
          ))}
        </ul>
      )}
      <h4>CRITICAL today</h4>
      {newestFirst.length === 0 ? (
        <p className="muted">none today</p>
      ) : (
        <ul aria-label="criticals today">
          {newestFirst.map((c, i) => (
            <li key={`${c.at}-${i}`} className="pnl-neg">
              <span className="muted">{c.at.length >= 16 ? c.at.slice(11, 19) : c.at}</span> {c.message}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
