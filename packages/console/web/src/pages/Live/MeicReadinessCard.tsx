import type { ReactNode } from "react";
import type { LiveMeicReadiness } from "@console/shared";
import { Card } from "../../components/DataTable";
import { HelpSection, type CardHelpContent } from "../../components/CardHelp";
import { GuideLink } from "../../components/GuideLink";

/**
 * Whether MEIC's live loop may be armed, as the module answers it (`arm_verdict`, bridged by the
 * server). The card draws the verdict, the held ICs and the loop's CRITICAL lines as given; it
 * computes nothing, and it has no button: arming is the human's `/live-meic-start`. Since 2026-11-06 it
 * leads the page and carries the arm, halt and gate tiles the page used to keep in a card of their own.
 *
 * Three states: ready, not ready (each blocker listed), and a bridge that failed -- which says so
 * and is never drawn as ready.
 */

export const READINESS_HELP: CardHelpContent = {
  title: "Readiness",
  body: (
    <>
      <HelpSection title="Ready to arm">
        <p>Every check the arm needs: the gate, the broker session, the stream and the ledger. Blockers are red, warnings amber. The verdict is the module&apos;s own.</p>
      </HelpSection>
      <HelpSection title="Halt flag">
        <p>It blocks new condors only: fills are still confirmed and open condors still stopped and closed. Clearing it arms nothing.</p>
      </HelpSection>
      <GuideLink to="/help/live#meic-and-flies" />
    </>
  ),
};

export function MeicReadinessCard({
  readiness,
  tiles,
  shown = () => false,
}: {
  readiness: LiveMeicReadiness | undefined;
  /** The arm, disarm, halt, gate, orphan and held tiles, drawn under the verdict. */
  tiles?: ReactNode;
  /** True for a held condor the open board already shows, so its line (and its stop) is said once. */
  shown?: (ic: string) => boolean;
}) {
  if (readiness === undefined || !readiness.ok) {
    return (
      <Card title="live readiness" collapseKey="live-meic-readiness" className="pf-cq" help={READINESS_HELP}>
        <p className="pnl-neg">
          <strong>Readiness unknown</strong> — the status read failed, so nothing here says live is ready
        </p>
        <p className="muted">{readiness === undefined ? "the server sent no readiness block" : readiness.error}</p>
        {tiles}
      </Card>
    );
  }
  const newestFirst = [...readiness.criticals].reverse();
  const held = readiness.openIcs.filter((ic) => !shown(ic));
  return (
    <Card title="live readiness" collapseKey="live-meic-readiness" className="pf-cq" help={READINESS_HELP}>
      <div className={`pf-hero ${readiness.ready ? "pnl-pos" : "pnl-neg"}`}>{readiness.ready ? "Ready to arm" : "Not ready"}</div>
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
      {tiles}
      {readiness.openIcs.length === 0 ? (
        <>
          <h4>Held now</h4>
          <p className="muted">no iron condor held</p>
        </>
      ) : held.length > 0 ? (
        <>
          <h4>Held now</h4>
          <ul aria-label="held iron condors">
            {held.map((ic) => (
              <li key={ic}>{ic}</li>
            ))}
          </ul>
        </>
      ) : null}
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
    </Card>
  );
}
