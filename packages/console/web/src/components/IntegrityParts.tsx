import type { MeasurementBreak } from "@console/shared";
import { fmtFrac } from "../lib/format";
import { MeasurementBreaks } from "./MeasurementBreaks";

/**
 * The one paragraph every module's integrity strip writes the same way: the ledger holds columns this
 * console build does not know. The rest of a strip is per-module and stays in the module's own file.
 */
export function SchemaDriftNote({ drift }: { drift: string[] }) {
  if (drift.length === 0) return null;
  return (
    <p className="integrity-err">
      <strong>Schema drift.</strong> The ledger holds {drift.length} column
      {drift.length === 1 ? "" : "s"} this console build does not know ({drift.join(", ")}). The module's writer has
      moved on — this page may be reading a narrower story than it is recording.
    </p>
  );
}

/** The strip's closing block: schema drift, then the journaled measurement breaks. Nothing when there is neither. */
export function IntegrityAlerts({ drift, breaks }: { drift: string[]; breaks: MeasurementBreak[] }) {
  if (drift.length === 0 && breaks.length === 0) return null;
  return (
    <div className="integrity-integrity-alerts">
      <SchemaDriftNote drift={drift} />
      <MeasurementBreaks breaks={breaks} />
    </div>
  );
}

/**
 * `1,204 marks · 3 refused (0.2%)`: how many reads a session made and how many the module refused to
 * price. `refusedLabel` is the module's word for the refusal ("refused", or "unmeasured" for a tick). The
 * refused count turns to a warning once it is above zero, and a share that was never computed is a dash.
 */
export function CoverageLine({
  count,
  noun,
  refused,
  share,
  refusedLabel = "refused",
}: {
  count: number;
  noun: string;
  refused: number;
  share: number | null;
  refusedLabel?: string;
}) {
  return (
    <p>
      {count.toLocaleString()} {noun} ·{" "}
      <span className={refused > 0 ? "integrity-warn" : ""}>
        {refused.toLocaleString()} {refusedLabel} ({fmtFrac(share, 1)})
      </span>
    </p>
  );
}
