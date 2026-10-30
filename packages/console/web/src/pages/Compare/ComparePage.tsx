import { Fragment } from "react";
import { useSearchParams } from "react-router-dom";
import type { CompareArm, CompareBreak, CompareDoc, CompareModule, CompareScope } from "@console/shared";
import { sortRows, type SortDir, type SortState } from "../../components/DataTable";
import { compareColumns, type CompareColumn } from "./compareColumns";
import { NetHistogram } from "./NetHistogram";
import { useCompare } from "./useCompare";

/**
 * Every arm beside its control (`GET /api/compare`, `core.compare`).
 *
 * The URL carries everything a reader would send someone: the scope (`?scope=all`; the era is the
 * default, per "a module's own evidence window is the default"), the sort (`?sort=net&dir=asc`,
 * one for every module's table) and the arm whose trade histogram is open (`?arm=flies/control`).
 *
 * **No verdicts.** The bridge labels nothing better or worse and neither does this page: no
 * significance colours, no pass/fail chips, no console-side thresholds. Tone is a number's sign.
 */

export function scopeFrom(v: string | null): CompareScope {
  return v === "all" ? "all" : "era";
}

const ARM_COL = "arm";

/** The sort in the URL, cycling as `useSort` does: desc, then asc, then the bridge's own order. */
export function nextSort(key: string | null, dir: SortDir, clicked: string): { key: string | null; dir: SortDir } {
  if (key !== clicked) return { key: clicked, dir: "desc" };
  if (dir === "desc") return { key, dir: "asc" };
  return { key: null, dir: "desc" };
}

export function sortArms(arms: CompareArm[], key: string | null, dir: SortDir, columns: CompareColumn[]): CompareArm[] {
  const accessors: Record<string, (a: CompareArm) => number | string | null> = { [ARM_COL]: (a) => a.arm };
  for (const c of columns) if (c.sort !== undefined) accessors[c.id] = c.sort;
  const state: SortState = { key, dir, toggle: () => undefined };
  return sortRows(arms, state, accessors);
}

export function ComparePage() {
  const [params, setParams] = useSearchParams();
  const scope = scopeFrom(params.get("scope"));
  const { data, isLoading, isError } = useCompare(scope);

  const set = (patch: Record<string, string | null>) => {
    const next = new URLSearchParams(params);
    for (const [k, v] of Object.entries(patch)) {
      if (v === null) next.delete(k);
      else next.set(k, v);
    }
    setParams(next, { replace: true });
  };

  const scopeControl = (
    <select
      className={`chip review-session-select ${scope === "era" ? "" : "scope-select-off-default"}`}
      value={scope}
      onChange={(e) => set({ scope: e.target.value === "all" ? "all" : null })}
      aria-label="scope"
      title="The default is each module's own evidence window (its era). 'all' pools every row the ledger holds, across measurement breaks — a stated widening, never the default."
    >
      <option value="era">each module's era</option>
      <option value="all">all rows (pooled across breaks)</option>
    </select>
  );

  let body;
  if (data === undefined) {
    body = isLoading ? (
      <span className="skeleton skeleton-text" style={{ width: "40%" }} />
    ) : (
      <p className="muted">{isError ? "The console could not reach /api/compare." : "No comparison to show."}</p>
    );
  } else if (!data.ok || data.data === null) {
    body = (
      <section className="card">
        <div className="card-head">
          <h2>The comparison could not be read</h2>
        </div>
        <p className="cmp-error">{data.error ?? "the compare bridge failed without a reason"}</p>
      </section>
    );
  } else {
    const sort = params.get("sort");
    const dir: SortDir = params.get("dir") === "asc" ? "asc" : "desc";
    body = (
      <CompareBody
        doc={data.data}
        sortKey={sort}
        sortDir={dir}
        openArm={params.get("arm")}
        onSort={(k) => {
          const n = nextSort(sort, dir, k);
          set({ sort: n.key, dir: n.key === null ? null : n.dir });
        }}
        onOpen={(id) => set({ arm: params.get("arm") === id ? null : id })}
      />
    );
  }

  return (
    <div className="cmp-page">
      <div className="cmp-controls">{scopeControl}</div>
      {body}
    </div>
  );
}

export function CompareBody({
  doc,
  sortKey,
  sortDir,
  openArm,
  onSort,
  onOpen,
}: {
  doc: CompareDoc;
  sortKey: string | null;
  sortDir: SortDir;
  openArm: string | null;
  onSort: (key: string) => void;
  onOpen: (id: string) => void;
}) {
  const levelPct = `${String(Math.round(doc.level * 100))}%`;
  const columns = compareColumns(doc.costs_note, levelPct);
  return (
    <>
      <section className="card cmp-method">
        <p>
          Session {doc.session}, scope <strong>{doc.scope === "era" ? "each module's own era" : "all rows, pooled across breaks"}</strong>.
          Intervals are {levelPct} session-bootstrap intervals ({doc.resamples.toLocaleString("en-US")} resamples, seeded with {doc.seed}, so a
          re-run gives the same interval). The paired figures use {doc.pairing}; a session only one side closed is counted, never filled
          with zero. "Sessions until" is a projection at the observed mean and spread — how many more sessions the interval would need to
          exclude zero if neither moved — not a forecast. Nothing here is labelled better or worse.
        </p>
        <p className="muted">{doc.costs_note}</p>
      </section>
      {doc.modules.map((m) => (
        <ModuleSection
          key={m.module}
          m={m}
          scope={doc.scope}
          session={doc.session}
          columns={columns}
          sortKey={sortKey}
          sortDir={sortDir}
          openArm={openArm}
          onSort={onSort}
          onOpen={onOpen}
        />
      ))}
    </>
  );
}

function BreakLine({ b }: { b: CompareBreak }) {
  return (
    <li>
      <span className="cmp-break-date">{b.break_date}</span> <span className="cmp-break-kind">{b.kind}</span>: {b.reason}
    </li>
  );
}

function EraBlock({ m, scope, session }: { m: CompareModule; scope: CompareScope; session: string }) {
  const era = m.era;
  if (era === null) return null;
  const armStarts = Object.entries(era.arm_starts);
  return (
    <div className="cmp-era">
      {scope === "era" ? (
        era.start === null ? (
          <p>No measurement break bounds this module: every row counts.</p>
        ) : (
          <p>
            Era from <strong>{era.start}</strong>
            {era.era_stamp !== null && <> (era stamp {era.era_stamp})</>}
            {era.bounding_break !== null && (
              <>
                , bounded by <span className="cmp-break-kind">{era.bounding_break.kind}</span> on {era.bounding_break.break_date}:{" "}
                <span className="muted">{era.bounding_break.reason}</span>
              </>
            )}
          </p>
        )
      ) : (
        <p>
          Every row the ledger holds, pooled across breaks
          {era.start !== null && <> (this module's era would start {era.start})</>}.
        </p>
      )}
      {scope === "era" && armStarts.length > 0 && (
        <p className="muted">Arms added inside the era start on their own date: {armStarts.map(([a, d]) => `${a} from ${d}`).join(", ")}.</p>
      )}
      {era.caveats.length > 0 && (
        <details className="cmp-caveats">
          <summary>
            {era.caveats.length} caveat{era.caveats.length === 1 ? "" : "s"} (journaled, not bounding)
          </summary>
          <ul>
            {era.caveats.map((b, i) => (
              <BreakLine key={i} b={b} />
            ))}
          </ul>
        </details>
      )}
      {era.ignored_future.length > 0 && (
        <p className="muted">
          {era.ignored_future.length} break{era.ignored_future.length === 1 ? "" : "s"} dated after {session} bind nothing yet.
        </p>
      )}
    </div>
  );
}

function armLabel(a: CompareArm): string {
  if (a.start_reason === "arm_added") return `from ${a.start ?? "—"}, when the arm was added`;
  if (a.start_reason === "first_row") return `from its first row, ${a.start ?? "—"}`;
  return "";
}

function ModuleSection({
  m,
  scope,
  session,
  columns,
  sortKey,
  sortDir,
  openArm,
  onSort,
  onOpen,
}: {
  m: CompareModule;
  scope: CompareScope;
  session: string;
  columns: CompareColumn[];
  sortKey: string | null;
  sortDir: SortDir;
  openArm: string | null;
  onSort: (key: string) => void;
  onOpen: (id: string) => void;
}) {
  const row = columns.filter((c) => c.secondary !== true);
  const secondary = columns.filter((c) => c.secondary === true);
  const own = row.filter((c) => c.group === undefined);
  const vs = row.filter((c) => c.group === "vs");
  const arms = sortArms(m.arms, sortKey, sortDir, columns);
  const span = 1 + row.length;

  const th = (id: string, header: string, title: string, sortable: boolean, num: boolean) => {
    const active = sortKey === id;
    return (
      <th
        key={id}
        title={title}
        className={num ? "num" : undefined}
        aria-sort={active ? (sortDir === "asc" ? "ascending" : "descending") : "none"}
        onClick={sortable ? () => onSort(id) : undefined}
        style={sortable ? { cursor: "pointer", userSelect: "none" } : undefined}
        data-sort={id}
      >
        {header}
        {active ? (sortDir === "asc" ? " ▲" : " ▼") : ""}
      </th>
    );
  };

  return (
    <section className="card cmp-module" data-module={m.module}>
      <div className="card-head">
        <h2>{m.module}</h2>
        <span className="chip" title="the ledger schema core.ledgers reads this module with">
          {m.trade_schema}
        </span>
        {m.status !== "ok" && <span className="chip">{m.status}</span>}
      </div>
      {m.status !== "ok" ? (
        <p className="cmp-status">
          {m.status === "absent" ? "No ledger on this machine" : "The read failed"}: {m.reason ?? "no reason given"}
        </p>
      ) : (
        <>
          <EraBlock m={m} scope={scope} session={session} />
          {arms.length === 0 ? (
            <p className="muted">
              {scope === "era" && m.era?.start != null
                ? "No arm has closed a trade inside this era yet; the scope control widens to every row."
                : "No arm has closed a trade."}
            </p>
          ) : (
            <div className="table-scroll">
              <table className="data-table cmp-table">
                <thead>
                  <tr>
                    <th colSpan={1 + own.length} />
                    <th colSpan={vs.length} className="cmp-group" title="this arm against its control, over the sessions both closed">
                      vs control
                    </th>
                  </tr>
                  <tr>
                    {th(ARM_COL, "arm", "the configured variant, run as its own portfolio; select one for its per-trade net histogram", true, false)}
                    {row.map((c) => th(c.id, c.header, c.title, c.sort !== undefined, c.num === true))}
                  </tr>
                </thead>
                <tbody>
                  {arms.map((a) => {
                    const id = `${m.module}/${a.arm}`;
                    const open = openArm === id;
                    const note = armLabel(a);
                    return (
                      <Fragment key={a.arm}>
                        <tr className={open ? "cmp-row-open" : undefined} data-arm={a.arm}>
                          <td>
                            <button
                              type="button"
                              className="cmp-arm"
                              aria-expanded={open}
                              title="show this arm's per-trade net histogram"
                              onClick={() => onOpen(id)}
                            >
                              {open ? "▾" : "▸"} {a.arm}
                            </button>
                            {a.experiment_status !== null && <span className="muted cmp-sub"> experiment {a.experiment_status}</span>}
                            {note !== "" && <div className="muted cmp-sub">{note}</div>}
                          </td>
                          {row.map((c) => (
                            <td key={c.id} className={c.num === true ? "num" : undefined} data-col={c.id}>
                              {c.render(a)}
                            </td>
                          ))}
                        </tr>
                        {open && (
                          <tr className="cmp-hist-row">
                            <td colSpan={span}>
                              <table className="data-table secondary-cells">
                                <thead>
                                  <tr>
                                    {secondary.map((c) => (
                                      <th key={c.id} title={c.title} className={c.num === true ? "num" : undefined}>
                                        {c.group === "vs" ? `vs control: ${c.header}` : c.header}
                                      </th>
                                    ))}
                                  </tr>
                                </thead>
                                <tbody>
                                  <tr>
                                    {secondary.map((c) => (
                                      <td key={c.id} className={c.num === true ? "num" : undefined} data-col={c.id}>
                                        {c.render(a)}
                                      </td>
                                    ))}
                                  </tr>
                                </tbody>
                              </table>
                              <div className="cmp-hist-head muted">
                                {a.arm}: net per closed trade, {a.trades} trade{a.trades === 1 ? "" : "s"}
                                {a.first !== null && a.last !== null ? `, ${a.first} to ${a.last}` : ""}. Bars are toned by which side of
                                zero their bin lies.
                              </div>
                              <NetHistogram values={a.trade_nets} label={`${m.module} ${a.arm}`} />
                            </td>
                          </tr>
                        )}
                      </Fragment>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </>
      )}
    </section>
  );
}
