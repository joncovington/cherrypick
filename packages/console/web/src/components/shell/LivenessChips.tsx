import { Link } from "react-router-dom";
import { useDesk } from "../../lib/api";
import { ageLabel } from "../../lib/format";
import { isModuleId } from "../../lightbox/moduleOrder";

/**
 * The header's per-producer liveness strip: age of the last event/iteration against each
 * producer's own declared cadence, so a stalled feed cannot look like a quiet market. Lives in
 * the global `StatusHeader` (2026-09) beside the clock and market-data chip, so it's the same
 * on every page rather than only on Overview.
 *
 * `cadenceSeconds === null` means the cadence could not be read -- the chip still shows the age,
 * with no colour judgement, rather than guessing a threshold.
 */
export function LivenessChips() {
  const { data } = useDesk();
  if (data === undefined) {
    return (
      <>
        <span className="skeleton skeleton-chip" />
        <span className="skeleton skeleton-chip" />
      </>
    );
  }
  return (
    <>
      {data.liveness.map((p) => {
        const over = p.overBy !== null;
        const cls = p.ageSeconds === null ? "chip" : over ? "chip-missing" : "chip-ok";
        const title =
          p.cadenceSeconds !== null
            ? `${p.kind} · cadence ${String(p.cadenceSeconds)}s${over ? ` · ${ageLabel(p.overBy)} over` : ""}`
            : `${p.kind} · cadence unknown`;
        const body = (
          <>
            {p.label} {ageLabel(p.ageSeconds)}
            {over && " ⚠"}
          </>
        );
        return isModuleId(p.id) ? (
          <Link key={p.id} to={`/${p.id}`} className={`chip ${cls}`} title={title}>
            {body}
          </Link>
        ) : (
          <span key={p.id} className={`chip ${cls}`} title={title}>
            {body}
          </span>
        );
      })}
    </>
  );
}
