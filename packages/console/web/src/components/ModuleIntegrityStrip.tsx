import type { ModuleIntegrity } from "@console/shared";
import { Card } from "./DataTable";
import { MeasurementBreaks } from "./MeasurementBreaks";

/**
 * What bounds how far a page's numbers can be trusted — above them rather than beneath, the
 * curve/pmcc/calendars precedent.
 *
 * This is the plain version, for the modules whose integrity is entirely "what breaks are on file
 * and is this build reading the whole ledger". The modules with a bound of their own (curve's
 * assignment exposure, bwb's trigger coverage, pmcc's early-assignment flag) keep their own strip:
 * those facts are genuinely different from each other and sharing them would invent an abstraction
 * over things that have nothing in common.
 *
 * It renders nothing when there is nothing to say. A permanently-present empty card teaches a
 * reader to stop looking at it, which is the opposite of the point — and unlike a coverage figure,
 * "no breaks recorded" is not itself a measurement worth a row.
 */
export function ModuleIntegrityStrip({
  integrity,
  collapseKey,
  updatedAt,
  children,
}: {
  integrity: ModuleIntegrity | undefined;
  collapseKey: string;
  updatedAt?: number;
  children?: React.ReactNode;
}) {
  const breaks = integrity?.measurementBreaks ?? [];
  const drift = integrity?.schemaDrift ?? [];
  if (breaks.length === 0 && drift.length === 0 && children === undefined) return null;

  return (
    <Card title="measurement integrity" collapseKey={collapseKey} updatedAt={updatedAt} className="view-fade">
      <div className="pmcc-integrity">
        <div className="integrity-integrity-grid">{children}</div>
        {drift.length > 0 && (
          <p className="integrity-err">
            <strong>Schema drift.</strong> The ledger holds {drift.length} column
            {drift.length === 1 ? "" : "s"} this console build does not know ({drift.join(", ")}). The module's
            writer has moved on — this page may be reading a narrower story than it is recording.
          </p>
        )}
        <MeasurementBreaks breaks={breaks} />
      </div>
    </Card>
  );
}
