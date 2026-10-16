import type { FliesSelectorSessions, SelectorCell } from "@console/shared";
import { Card, PnlCell, SortTh, sortRows, useSort } from "../../components/DataTable";
import { fmtMoney } from "../../lib/format";
import { useBridged } from "../../lib/bridged";

/**
 * The selector arm (`run.py selector-sessions`, `analytics.selector_sessions`, bridged): what it
 * chose each session, whether the session DEPARTED from control (it booked something other than
 * exactly control's trades -- the only sessions its read counts), its net against control's, and
 * each frozen model read under procedure v2: a cell's 90% session interval and whether it sits
 * wholly clear of zero, the only thing that lets the selector depart or skip (`selector._clear`).
 *
 * The first read is at ten departing sessions, per-session net, sign test (experiment log
 * 2026-10-04, amended 2026-10-12); the module withholds the p-value until then and so does this page.
 */

function interval(v: [number, number] | null): string {
  return v === null ? "—" : `[${fmtMoney(v[0])}, ${fmtMoney(v[1])}]`;
}

function clearLabel(c: SelectorCell): string {
  if (!c.eligible) return "thin";
  if (c.clear_above) return "clear above 0";
  if (c.clear_below) return "clear below 0";
  return "spans 0";
}

function CellsTable({ cells }: { cells: SelectorCell[] }) {
  const sort = useSort();
  if (cells.length === 0) return <p className="muted">no cells in this model</p>;
  // The interval and the reading stay unsorted: a pair of bounds is no one number, and the reading
  // is a label derived from it.
  const rows = sortRows(cells, sort, {
    cell: (c) => c.cell,
    source: (c) => c.source,
    sessions: (c) => c.sessions,
    entries: (c) => c.entries,
    net: (c) => c.net_per_entry,
  });
  return (
    <div className="table-scroll">
      <table className="data-table num-from-2">
        <thead>
          <tr>
            <SortTh label="cell" k="cell" sort={sort} title="vol bucket | trend bucket" />
            <SortTh label="source" k="source" sort={sort} />
            <SortTh label="sessions" k="sessions" sort={sort} />
            <SortTh label="entries" k="entries" sort={sort} />
            <SortTh label="net / entry" k="net" sort={sort} />
            <th title="90% seeded-bootstrap interval on the cell's total net (core.regimecuts.robustness)">interval</th>
            <th title="v2 acts only on a cell whose interval is wholly clear of zero; below min_sessions a cell is never evidence">reading</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((c) => (
            <tr key={`${c.cell}-${c.source}`}>
              <td>{c.cell}</td>
              <td>{c.source}</td>
              <td className="muted">{c.sessions ?? "—"}</td>
              <td className="muted">{c.entries ?? "—"}</td>
              <td><PnlCell v={c.net_per_entry} /></td>
              <td className="muted">{interval(c.interval)}</td>
              <td className={c.clear_above || c.clear_below ? "" : "muted"}>{clearLabel(c)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function SelectorSlide() {
  const { data, isLoading, dataUpdatedAt } = useBridged<FliesSelectorSessions>(["flies-selector"], "/api/flies/selector");
  const d = data?.data ?? null;

  if (isLoading) {
    return (
      <div className="cards cards-wide">
        <Card title="selector">
          <span className="skeleton skeleton-text" style={{ width: "50%" }} />
        </Card>
      </div>
    );
  }
  if (data === undefined || !data.ok || d === null) {
    return (
      <div className="cards cards-wide">
        <Card title="selector" isError>
          <p className="muted">{data?.error ?? "the selector read is unavailable"}</p>
        </Card>
      </div>
    );
  }
  if (d.ledger === "absent") {
    return (
      <div className="cards cards-wide">
        <Card title="selector">
          <p className="muted">No flies paper ledger on this machine.</p>
        </Card>
      </div>
    );
  }

  const dep = d.departing;
  const latest = d.latest_model ?? null;
  return (
    <div className="cards cards-wide">
      <Card title="selector — departures from control" updatedAt={dataUpdatedAt}>
        <p className="muted" style={{ fontSize: 12 }}>
          Procedure v{String(d.procedure_version ?? "—")} over {(d.features ?? []).join(" × ")}. A session counts only
          when the selector booked something other than exactly what control booked. The first read comes at{" "}
          <strong>{String(dep?.first_read_at ?? 10)}</strong> departing sessions, on per-session net against control.
        </p>
        {d.sessions.length === 0 ? (
          <p className="muted">The selector has not traded yet — no choices are recorded in the paper ledger.</p>
        ) : (
          <>
            <p style={{ fontSize: 13 }}>
              <strong>{String(dep?.sessions ?? 0)}</strong> departing session{dep?.sessions === 1 ? "" : "s"} of{" "}
              {String(d.sessions.length)} · ahead {String(dep?.ahead ?? 0)} / behind {String(dep?.behind ?? 0)} · Σ diff{" "}
              {fmtMoney(dep?.diff ?? null)}
              {dep?.first_read != null ? (
                <>
                  {" "}· first read (first {String(dep.first_read_at)}): ahead {String(dep.first_read.ahead)} / behind{" "}
                  {String(dep.first_read.behind)}, sign test p {dep.first_read.sign_test_p ?? "—"}
                </>
              ) : (
                <span className="muted"> · first read not due ({String(dep?.sessions ?? 0)} of {String(dep?.first_read_at ?? 10)})</span>
              )}
            </p>
            <div className="table-scroll">
              <table className="data-table num-from-2">
                <thead>
                  <tr>
                    <th>session</th>
                    <th title="booked something other than exactly control's trades">departed</th>
                    <th title="ticks whose choice was a departure by construction (model_preferred, default_cell_negative) / all ticks">departing ticks</th>
                    <th title="choice reasons, by tick">reasons</th>
                    <th title="the source label each booked position came from">booked from</th>
                    <th title="entries: selector / control">entries</th>
                    <th>selector net</th>
                    <th>control net</th>
                    <th>diff</th>
                    <th title="the session's frozen model">model</th>
                  </tr>
                </thead>
                <tbody>
                  {d.sessions.map((s) => (
                    <tr key={s.session}>
                      <td>{s.session}</td>
                      <td>{s.departed ? "departed" : <span className="muted">twin</span>}</td>
                      <td className="muted">{`${String(s.departing_ticks)} / ${String(s.ticks)}`}</td>
                      <td className="muted" style={{ fontSize: 11 }}>
                        {Object.entries(s.reasons).map(([r, n]) => `${r} ×${String(n)}`).join(" · ") || "—"}
                      </td>
                      <td className="muted" style={{ fontSize: 11 }}>
                        {Object.entries(s.selected_from).map(([r, n]) => `${r} ×${String(n)}`).join(" · ") || "—"}
                      </td>
                      <td className="muted">{`${String(s.selector_entries)} / ${String(s.control_entries)}`}</td>
                      <td>{s.selector_net === null ? <span className="muted">open</span> : <PnlCell v={s.selector_net} />}</td>
                      <td>{s.control_net === null ? <span className="muted">open</span> : <PnlCell v={s.control_net} />}</td>
                      <td><PnlCell v={s.diff} /></td>
                      <td className="muted">{s.model.ok ? (s.model_ids[0] ?? "ok") : s.model.reason}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </>
        )}
      </Card>
      <Card title={`newest model${latest?.session != null ? ` — for ${latest.session}` : ""}`} updatedAt={dataUpdatedAt}>
        {latest === null ? (
          <p className="muted">No selector model has been fitted on this machine (<code>run.py selector-fit --write</code>).</p>
        ) : !latest.ok ? (
          <p>
            <span className="chip chip-warn">{latest.reason}</span>{" "}
            <span className="muted">
              the model on disk for {latest.session ?? "—"} is procedure v{String(latest.procedure_version ?? "?")}, fitted
              through {latest.fitted_through ?? "—"}; the selector would refuse it and stay control&apos;s twin
            </span>
          </p>
        ) : (
          <>
            <p className="muted" style={{ fontSize: 12 }}>
              {latest.model_id} · fitted through {latest.fitted_through ?? "—"} · sources start{" "}
              {Object.entries(latest.arm_starts ?? {}).map(([s, d0]) => `${s} ${d0 ?? "—"}`).join(", ")}
            </p>
            <CellsTable cells={latest.cells} />
          </>
        )}
      </Card>
    </div>
  );
}
