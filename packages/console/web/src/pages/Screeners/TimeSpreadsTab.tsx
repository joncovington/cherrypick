import { useState } from "react";
import { Card } from "../../components/DataTable";
import { TimeSpreadTable } from "../../components/TimeSpreadTable";
import { useTimeSpreads } from "../../lib/api";

/**
 * The time-spread scanner (docs/time-spread-scanner-plan.md): the day's calendars and diagonals on the
 * names whose front week is priced rich to the back month, unsized (Live, portfolio sizes them to the
 * account). Every figure is the scan's; this formats and decides nothing.
 *
 * NO TESTED EDGE: rows are ordered by the front/back IV ratio, the hypothesis item 54 tests.
 */
export function TimeSpreadsTab() {
  const [day, setDay] = useState<string | null>(null);
  const q = useTimeSpreads(day);
  const d = q.data;
  if (!d) return <p className="muted">{q.isError ? "the time-spread scan could not be read" : "loading…"}</p>;
  const dropped = Object.entries(d.dropped)
    .map(([why, n]) => `${n} ${why}`)
    .join(", ");
  const failing = d.terms.filter((t) => !t.passes);
  const why: Record<string, number> = {};
  for (const t of failing) for (const w of t.why) why[w] = (why[w] ?? 0) + 1;
  return (
    <Card
      title="time spreads"
      updatedAt={q.dataUpdatedAt}
      controls={
        d.days.length > 1 ? (
          <select value={d.day ?? ""} onChange={(e) => setDay(e.target.value || null)} aria-label="scan day">
            {d.days.map((x) => (
              <option key={x} value={x}>
                {x}
              </option>
            ))}
          </select>
        ) : null
      }
    >
      {d.error ? (
        <p className="muted">{d.error}</p>
      ) : (
        <>
          <p className="muted pf-caption" style={{ marginTop: 0 }}>
            <strong>No tested edge.</strong> {d.order ?? "ordered by the front/back IV ratio, untested"}. Calendars at the money and
            one strike either side, and put and call diagonals, on the {d.shortlist.length} best ratios of {d.passing ?? "—"} names that
            passed ({d.read ?? "—"} read of {d.universe ?? "—"} tradable). Modelled at mid from the {d.day} scan: re-quote before sending.
            {d.problem ? ` The scan is incomplete: ${d.problem}.` : ""}
          </p>
          {d.structures.length > 0 ? (
            <TimeSpreadTable rows={d.structures} />
          ) : (
            <p className="muted">no structure cleared its gates today</p>
          )}
          <p className="muted pf-caption">
            {dropped ? `left out: ${dropped}` : ""}
            {Object.keys(why).length > 0
              ? `${dropped ? " · " : ""}names that did not pass: ${Object.entries(why)
                  .map(([w, n]) => `${n} ${w}`)
                  .join(", ")}`
              : ""}
          </p>
          <p className="muted pf-caption">Sized to the account on Live, portfolio. Nothing here is placed.</p>
        </>
      )}
    </Card>
  );
}
