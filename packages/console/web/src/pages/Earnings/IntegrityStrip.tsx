import type { EarningsPayload } from "@console/shared";
import { Card } from "../../components/DataTable";
import { MeasurementBreaks } from "../../components/MeasurementBreaks";

/**
 * What bounds how far this page's numbers can be trusted — above them rather than beneath, the
 * curve/pmcc/calendars precedent.
 *
 * earnings is the one module with a live book beside a paper one, and it is the page's sharpest way
 * to mislead: the trade and review tables span BOTH books on purpose while the analytics and
 * strategy detail follow a mode toggle. The per-row badges say which book a row came from; nothing
 * said what the mix was, so a reader could take a blended table for one book's result.
 *
 * The breaks come from the paper ledger, which is where the methodology journal lives — the live
 * ledger has no such table — and they describe the module's RULES, which govern both books. This
 * schema records `old -> new` rather than a bare note, so the strip says what actually changed.
 */
export function IntegrityStrip({ data, updatedAt }: { data: EarningsPayload | undefined; updatedAt?: number }) {
  const integrity = data?.integrity;
  const breaks = integrity?.measurementBreaks ?? [];
  const drift = integrity?.schemaDrift ?? [];
  const books = integrity?.books;
  const detail = new Map((integrity?.breakDetail ?? []).map((d) => [d.key, d]));
  if (breaks.length === 0 && drift.length === 0 && books === undefined) return null;

  return (
    <Card title="measurement integrity" collapseKey="earnings-integrity" updatedAt={updatedAt} className="view-fade">
      <div className="pmcc-integrity">
        {books !== undefined && (
          <div className="integrity-integrity-banner">
            <strong>These tables span both books.</strong>{" "}
            {books.live === 0 ? (
              <>
                Every row in scope is <strong>paper</strong> ({books.paper.toLocaleString()}). The analytics and
                strategy detail follow the mode toggle above; these tables do not.
              </>
            ) : (
              <>
                <span className="integrity-warn">{books.live.toLocaleString()} live</span> and{" "}
                {books.paper.toLocaleString()} paper rows are in scope together. A blended net is not one book's
                result — the analytics and strategy detail follow the mode toggle above, these tables do not.
              </>
            )}
          </div>
        )}
        {drift.length > 0 && (
          <p className="integrity-err">
            <strong>Schema drift.</strong> The ledger holds {drift.length} column
            {drift.length === 1 ? "" : "s"} this console build does not know ({drift.join(", ")}). The module's
            writer has moved on — this page may be reading a narrower story than it is recording.
          </p>
        )}
        <MeasurementBreaks breaks={breaks} />
        {detail.size > 0 && breaks.length > 0 && (
          <ul className="integrity-plain-list">
            {breaks.map((b) => {
              const d = detail.get(b.key);
              if (d === undefined || (d.from === null && d.to === null)) return null;
              return (
                <li key={`d-${b.key}`}>
                  <span className="mono">{b.key}</span>
                  <span className="muted">
                    {" "}
                    {d.from ?? "—"} → {d.to ?? "—"}
                  </span>
                </li>
              );
            })}
          </ul>
        )}
      </div>
    </Card>
  );
}
