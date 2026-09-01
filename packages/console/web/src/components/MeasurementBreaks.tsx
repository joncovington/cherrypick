import type { MeasurementBreak } from "@console/shared";

/**
 * The journaled measurement breaks for one module.
 *
 * Extracted 2026-08-27. Four module strips carried a byte-identical copy of this block and two more
 * were about to be written; the rendering of "results either side of this date must not be pooled"
 * is the one part of an integrity strip that is genuinely the same everywhere, because the rule it
 * states is suite-wide rather than per-module. The rest of each strip stays where it is -- what
 * bounds a curve's net (assignment exposure) has nothing in common with what bounds a bwb tick
 * (trigger coverage), and folding those together would only invent a shared abstraction over two
 * different facts.
 *
 * `scope` is rendered when the ledger records one: meic and flies scope a break to a single arm, and
 * a reader who assumed every break applied to the whole book would over-state what is affected.
 */
export function MeasurementBreaks({ breaks }: { breaks: MeasurementBreak[] }) {
  if (breaks.length === 0) return null;
  return (
    <div className="integrity-err">
      <strong>Measurement breaks.</strong> Results either side of these dates must not be pooled.
      <ul className="integrity-plain-list">
        {breaks.map((b) => (
          <li key={`${b.date}-${b.key}-${b.scope ?? ""}`}>
            {b.date} · <span className="mono">{b.key}</span>
            {b.scope !== undefined && b.scope !== null && b.scope !== "*" && (
              <span className="chip"> {b.scope}</span>
            )}
            {b.note !== null && <span className="muted"> — {b.note}</span>}
          </li>
        ))}
      </ul>
    </div>
  );
}
