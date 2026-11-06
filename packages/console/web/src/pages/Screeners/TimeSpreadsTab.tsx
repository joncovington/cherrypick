import { useMemo, useState } from "react";
import type { ReactNode } from "react";
import { useSearchParams } from "react-router-dom";
import { Card } from "../../components/DataTable";
import { TimeSpreadScanTable } from "../../components/TimeSpreadScanTable";
import { useTimeSpreads } from "../../lib/api";
import {
  applyFilters,
  groupByName,
  joinAccount,
  type Direction,
  type Right,
  type StructureKind,
  type TimeSpreadFilters,
} from "../../lib/timeSpreadView";

/**
 * The time-spread scanner (docs/time-spread-scanner-plan.md): the day's calendars and diagonals on the
 * names whose front week is priced rich to the back month. Each row says whether the account's sizing
 * (the same day's manage evaluation) fits it, or which cap stops it. Chips filter and group the rows;
 * they live in the URL as the other Screeners tabs' do. Every figure is the scan's or manage's; this
 * formats and decides nothing, and nothing here stages or sends an order.
 *
 * NO TESTED EDGE: rows are ordered by the front/back IV ratio, the hypothesis item 54 tests.
 */

function Flag({ on, onChange, children, title }: { on: boolean; onChange: (on: boolean) => void; children: ReactNode; title: string }) {
  return (
    <div className="mode-toggle" style={{ marginLeft: 0 }}>
      <button type="button" title={title} aria-pressed={on} className={on ? "mode-btn active" : "mode-btn"} onClick={() => onChange(!on)}>
        {children}
      </button>
    </div>
  );
}

function pick<T extends string>(v: string | null, allowed: readonly T[]): T | null {
  return allowed.find((a) => a === v) ?? null;
}

function pct0(v: number): string {
  return `${Math.round(v * 100)}%`;
}

export function filtersFromParams(p: URLSearchParams): TimeSpreadFilters {
  const raw = p.get("maxdb");
  const max = Number(raw);
  return {
    structure: pick<StructureKind>(p.get("structure"), ["calendar", "diagonal"]),
    right: pick<Right>(p.get("right"), ["put", "call"]),
    direction: pick<Direction>(p.get("direction"), ["bullish", "neutral", "bearish"]),
    maxDebit: raw !== null && raw !== "" && Number.isFinite(max) && max > 0 ? max : null,
    fitsOnly: p.get("fits") === "1",
  };
}

export function TimeSpreadsTab() {
  const [params, setParams] = useSearchParams();
  const [day, setDay] = useState<string | null>(null);
  const q = useTimeSpreads(day);
  const d = q.data;
  const views = useMemo(() => joinAccount(d?.structures ?? [], d?.account ?? null), [d]);
  if (!d) return <p className="muted">{q.isError ? "the time-spread scan could not be read" : "loading…"}</p>;
  const filters = filtersFromParams(params);
  const grouped = params.get("group") === "1";
  const set = (key: string, value: string | null) => {
    const next = new URLSearchParams(params);
    if (value === null) next.delete(key);
    else next.set(key, value);
    setParams(next, { replace: true });
  };
  const toggleOf = <T extends string>(key: string, cur: T | null, v: T) => set(key, cur === v ? null : v);
  const shown = applyFilters(views, filters);
  const groups = grouped ? groupByName(shown) : [];
  const dropped = Object.entries(d.dropped)
    .map(([why, n]) => `${n} ${why}`)
    .join(", ");
  const failing = d.terms.filter((t) => !t.passes);
  const why: Record<string, number> = {};
  for (const t of failing) for (const w of t.why) why[w] = (why[w] ?? 0) + 1;
  const a = d.account;
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
          <p className="muted pf-caption">
            {a
              ? `Marked against the account's sizing${a.sourceSession ? ` (the ${a.sourceSession} evaluation)` : ""}: ${a.fitsTotal} fit, ${a.tooBigTotal} too big${
                  a.bpCapPct !== null ? `, buying-power cap ${pct0(a.bpCapPct)}` : ""
                }.${a.sourceSession && a.sourceSession !== d.day ? ` That evaluation sized the ${a.sourceSession} scan, so rows may not match.` : ""}`
              : `${d.accountNote ?? "no account sizing"}: rows are shown unmarked.`}
          </p>

          <div role="group" aria-label="time-spread filters" style={{ display: "flex", flexWrap: "wrap", gap: 8, alignItems: "center", marginBottom: 8 }}>
            {(["calendar", "diagonal"] as const).map((v) => (
              <Flag key={v} on={filters.structure === v} onChange={() => toggleOf("structure", filters.structure, v)} title={`Only ${v}s`}>
                {v}
              </Flag>
            ))}
            {(["put", "call"] as const).map((v) => (
              <Flag key={v} on={filters.right === v} onChange={() => toggleOf("right", filters.right, v)} title={`Only ${v} structures`}>
                {v}
              </Flag>
            ))}
            {(["bullish", "neutral", "bearish"] as const).map((v) => (
              <Flag key={v} on={filters.direction === v} onChange={() => toggleOf("direction", filters.direction, v)} title={`Only ${v} structures`}>
                {v}
              </Flag>
            ))}
            <label className="chip" title="Largest debit at the mid, per share">
              max debit{" "}
              <input
                type="number"
                min="0"
                step="0.25"
                aria-label="Maximum debit per share"
                style={{ width: "5em" }}
                value={params.get("maxdb") ?? ""}
                onChange={(e) => set("maxdb", e.target.value === "" ? null : e.target.value)}
              />
            </label>
            <Flag
              on={filters.fitsOnly}
              onChange={(on) => set("fits", on ? "1" : null)}
              title={a ? "Only rows the account's sizing fits" : "Needs the account's sizing, which is missing"}
            >
              fits only
            </Flag>
            <Flag on={grouped} onChange={(on) => set("group", on ? "1" : null)} title="One block a name, with its ratio, IV rank, spot and earnings">
              group by name
            </Flag>
            <span className="chip">
              {shown.length} of {views.length}
            </span>
          </div>

          {d.structures.length === 0 ? (
            <p className="muted">no structure cleared its gates today</p>
          ) : shown.length === 0 ? (
            <p className="muted">no structure matches these filters</p>
          ) : grouped ? (
            groups.map((g) => (
              <details key={g.underlying} open style={{ marginBottom: 8 }}>
                <summary>
                  <strong>{g.underlying}</strong>{" "}
                  <span className="muted">
                    ratio ×{g.ratio?.toFixed(2) ?? "—"} · IV rank {g.ivRank === null ? "—" : pct0(g.ivRank)} · spot{" "}
                    {g.spot === null ? "—" : `$${g.spot.toFixed(2)}`} · earnings {g.earningsDate ?? "—"} ·{" "}
                    {a ? `${g.fitCount} of ${g.rows.length} fit` : `${g.rows.length} rows`}
                  </span>
                </summary>
                <TimeSpreadScanTable rows={g.rows} />
              </details>
            ))
          ) : (
            <TimeSpreadScanTable rows={shown} />
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
