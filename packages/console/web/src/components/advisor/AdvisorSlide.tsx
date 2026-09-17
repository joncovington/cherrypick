import { useQuery } from "@tanstack/react-query";
import type {
  AdvisorActiveExperiment,
  AdvisorApplyStatus,
  AdvisorExperiment,
  AdvisorModulePayload,
  AdvisorSessionCell,
} from "@console/shared";
import { PairTable } from "../../pages/Advisor/AdvisorPage";
import { normalizeModulePayload } from "../../lib/advisorShape";
import { gateDistance } from "./experimentStats";

/**
 * One module's view of the advisor, rendered as an "advisor" slide inside the module's own
 * lightbox (2026-09-12). A reader looking at a module asks "is my A/B working"; the cross-module
 * advisor page answered that only after expanding a card per experiment.
 *
 * Since 2026-09-17 a module can run several experiments at once, each on its own advised book
 * (`advised:<experiment name>`), so nothing here assumes one: the running section, the session
 * strip, the comparison and tomorrow's artifact are all drawn per experiment.
 *
 * Everything here is read from `packages/advisor`'s own store through the console's reader. No
 * judgement is formed on this side: enactment statuses, verdicts and the stall budget are the
 * advisor's, and the comparison is as of the last evening pass, which the slide says.
 */

function useAdvisorModule(module: string) {
  return useQuery<AdvisorModulePayload>({
    queryKey: ["advisor-module", module],
    queryFn: async () => {
      const res = await fetch(`/api/advisor/module/${encodeURIComponent(module)}`);
      if (!res.ok) throw new Error(`advisor module: HTTP ${res.status}`);
      // Normalised at the boundary: a freshly built page can meet the previous build's API until
      // the supervisor restarts the server (`lib/advisorShape.ts`).
      return normalizeModulePayload(await res.json());
    },
    refetchInterval: 60_000,
  });
}

const STATUS_LABEL: Record<string, string> = {
  enacted: "applied",
  carried: "carried",
  not_enacted: "not applied",
  no_artifact: "nothing issued",
};

function money(v: unknown): string {
  return typeof v === "number" ? `${v < 0 ? "-" : ""}$${Math.abs(v).toFixed(2)}` : "—";
}

function stub(text: string | null, n = 220): string {
  if (text === null) return "";
  return text.length > n ? `${text.slice(0, n)}…` : text;
}

export function SessionStrip({ cells }: { cells: AdvisorSessionCell[] }) {
  if (cells.length === 0) return <span className="muted">no scored sessions yet</span>;
  return (
    <div className="advisor-strip" role="list" aria-label="scored sessions, oldest first">
      {cells.map((c) => (
        <span
          key={`${c.session}:${c.experimentId ?? ""}`}
          role="listitem"
          className={`advisor-cell advisor-cell-${c.status}`}
          title={`${c.session}: ${STATUS_LABEL[c.status] ?? c.status}${c.detail ? ` — ${c.detail}` : ""}`}
        />
      ))}
    </div>
  );
}

/**
 * The strip per experiment. A cell with no experiment id is the module's own row for a session
 * nothing was issued for; it is shown once, under the module, rather than against every experiment.
 */
export function SessionStrips({ cells, active }: { cells: AdvisorSessionCell[]; active: AdvisorExperiment[] }) {
  if (cells.length === 0) return <SessionStrip cells={cells} />;
  const ids = new Set(cells.map((c) => c.experimentId).filter((id): id is string => id !== null));
  const named = new Map(active.map((e) => [e.id, e.name ?? e.id]));
  const rows: Array<{ key: string; label: string; cells: AdvisorSessionCell[] }> = [];
  // Active experiments first in activation order, then any other id the strip still carries
  // (an experiment concluded within the window), then the module's own unattributed cells.
  for (const e of active) if (ids.has(e.id)) rows.push({ key: e.id, label: named.get(e.id)!, cells: cells.filter((c) => c.experimentId === e.id) });
  for (const id of ids) if (!named.has(id)) rows.push({ key: id, label: id, cells: cells.filter((c) => c.experimentId === id) });
  const bare = cells.filter((c) => c.experimentId === null);
  if (bare.length > 0) rows.push({ key: "", label: "no experiment", cells: bare });
  if (rows.length === 1) return <SessionStrip cells={rows[0]!.cells} />;
  return (
    <div className="advisor-strips">
      {rows.map((r) => (
        <div key={r.key} className="advisor-strip-row">
          <span className="muted advisor-strip-label">{r.label}</span>
          <SessionStrip cells={r.cells} />
        </div>
      ))}
    </div>
  );
}

function Progress({ e }: { e: AdvisorActiveExperiment }) {
  const calendar = e.calendarSessions;
  const budget = e.stallBudget;
  const pct = e.expiresAfter > 0 ? Math.min(100, (100 * e.sessionsRun) / e.expiresAfter) : 0;
  const agePct = budget > 0 && calendar !== null ? Math.min(100, (100 * calendar) / budget) : 0;
  return (
    <div className="advisor-progress">
      <div className="advisor-bar" title="sessions the module actually applied the advice, over the experiment's length">
        <div className="advisor-bar-fill" style={{ width: `${pct}%` }} />
      </div>
      <div className="muted">
        {e.sessionsRun} of {e.expiresAfter} sessions enacted
        {calendar !== null && (
          <>
            {" "}· {calendar} calendar session{calendar === 1 ? "" : "s"} since it started, stalls at {budget}
          </>
        )}
      </div>
      {calendar !== null && (
        <div className="advisor-bar advisor-bar-thin" title="calendar sessions against the stall budget (twice the length)">
          <div className={`advisor-bar-fill ${agePct >= 75 ? "advisor-bar-warn" : ""}`} style={{ width: `${agePct}%` }} />
        </div>
      )}
    </div>
  );
}

function ParamRows({ params }: { params: Record<string, unknown> }) {
  const entries = Object.entries(params);
  if (entries.length === 0) return <span className="muted">no overlay</span>;
  return (
    <table className="data-table data-table-labelled">
      <thead>
        <tr>
          <th>param</th>
          <th>advised</th>
        </tr>
      </thead>
      <tbody>
        {entries.map(([k, v]) => (
          <tr key={k}>
            <td>{k}</td>
            <td>{String(v)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function ArtifactEntries({ t }: { t: AdvisorApplyStatus }) {
  const entries = t.artifactExperiments;
  const rejectedCount = entries.reduce((n, e) => n + e.rejected.length, 0);
  return (
    <>
      <p>
        <span className="chip">written</span> for {t.nextSession}
        {entries.length > 1 && <span className="muted"> · {entries.length} experiments</span>}
        {rejectedCount > 0 && (
          <span
            className="chip chip-warn"
            title={entries.flatMap((e) => e.rejected.map((r) => `${r.param}: ${r.reason}`)).join("\n")}
          >
            {rejectedCount} rejected
          </span>
        )}
      </p>
      {entries.map((e, i) => (
        <div key={e.experimentId ?? e.tag ?? i}>
          {(entries.length > 1 || e.name !== null) && (
            <div className="muted">
              <strong>{e.name ?? e.experimentId ?? "experiment"}</strong>
              {e.tag !== null && <> · {e.tag}</>}
              {e.base !== null && <> against {e.base}</>}
            </div>
          )}
          {e.proposals.length === 0 ? (
            <p className="muted">nothing admitted — runs its baseline</p>
          ) : (
            <ul className="muted">
              {e.proposals.map((p) => (
                <li key={p.param}>
                  {p.param} = {String(p.value)}
                </li>
              ))}
            </ul>
          )}
        </div>
      ))}
    </>
  );
}

function Tomorrow({ data }: { data: AdvisorModulePayload }) {
  const t = data.tomorrow;
  if (t === null) return <p className="muted">no apply status for this module yet</p>;
  if (t.disabledReason !== null)
    return (
      <p>
        <span className="chip chip-missing">advice off</span> <span className="muted">{t.disabledReason}</span>
      </p>
    );
  if (!t.artifactWritten)
    return (
      <p className="muted">
        no artifact written for {t.nextSession ?? "the next session"} — the module runs its baseline
      </p>
    );
  return <ArtifactEntries t={t} />;
}

function ConcludedList({ items }: { items: AdvisorExperiment[] }) {
  if (items.length === 0) return <p className="muted">nothing concluded on this module yet</p>;
  return (
    <ul className="advisor-concluded">
      {items.map((e) => (
        <li key={e.id}>
          <strong>{e.name ?? e.id}</strong> <span className={`chip ${e.status === "killed" ? "chip-warn" : "chip-missing"}`}>{e.status}</span>{" "}
          <span className="muted">
            {e.sessionsRun} of {e.expiresAfter} sessions
            {e.verdict?.stalled != null && " · stalled"}
            {e.verdict?.underpowered === true && " · underpowered"}
            {e.verdict?.recommendation != null && ` · model: ${e.verdict.recommendation.value}`}
          </span>
          {e.verdict?.recommendation != null && (
            <div className="muted">{stub(e.verdict.recommendation.rationale)}</div>
          )}
        </li>
      ))}
    </ul>
  );
}

function RunningExperiment({ e, module }: { e: AdvisorActiveExperiment; module: string }) {
  return (
    <section className="card">
      <h2>
        Running on {module}
        <span className="muted"> · {e.name ?? e.id}</span>
      </h2>
      <p className="muted">
        {e.tag ?? `advised:${e.baseProfile}`} against {e.baseProfile}
      </p>
      <p className="muted">{stub(e.hypothesis)}</p>
      <ParamRows params={e.params} />
      <Progress e={e} />
    </section>
  );
}

/** The slide's body, pure over its payload so it can be rendered and asserted without a client. */
export function AdvisorSlideBody({ data }: { data: AdvisorModulePayload }) {
  if (!data.storePresent) {
    return (
      <section className="card">
        <h2>Advisor</h2>
        <p className="muted">the advisor has not run yet — nothing to show for {data.module}</p>
      </section>
    );
  }
  const active = data.active;
  return (
    <div className="cards cards-wide">
      {active.length === 0 ? (
        <section className="card">
          <h2>Running on {data.module}</h2>
          <p className="muted">
            no active experiment{data.queued.length > 0 ? ` — ${data.queued.length} queued, activates at the next evening pass` : ""}
          </p>
        </section>
      ) : (
        active.map((e) => <RunningExperiment key={e.id} e={e} module={data.module} />)
      )}

      <section className="card">
        <h2>Session by session</h2>
        <SessionStrips cells={data.sessions} active={active} />
        <div className="advisor-legend muted">
          <span>
            <i className="advisor-cell advisor-cell-enacted" /> applied
          </span>
          <span>
            <i className="advisor-cell advisor-cell-carried" /> carried
          </span>
          <span>
            <i className="advisor-cell advisor-cell-not_enacted" /> not applied
          </span>
          <span>
            <i className="advisor-cell advisor-cell-no_artifact" /> nothing issued
          </span>
        </div>
        <p className="muted">
          Only an applied session advances the count. Carried means the params were already frozen on
          positions the module still held; nothing new was decided and nothing was charged.
          {active.length > 1 && " One strip per experiment: each is scored on its own book."}
        </p>
      </section>

      {active
        .filter((e) => e.verdict !== null)
        .map((e) => (
          <section className="card" key={`verdict-${e.id}`}>
            <h2>
              {e.name ?? e.id} against {e.baseProfile}
              <span className="muted"> · as of the last evening pass</span>
            </h2>
            <PairTable pairs={e.verdict!.pairs} />
            {(() => {
              const d = e.verdict!.pairs[0]?.delta;
              const gate = gateDistance(e);
              return (
                <p className="muted">
                  {d !== undefined && <>net delta {money(d["net_pnl"])}</>}
                  {gate !== null && <> · gate: {gate}</>}
                  {e.verdict!.underpowered && <> · below the gate, so not yet measured</>}
                </p>
              );
            })()}
          </section>
        ))}

      <section className="card">
        <h2>Tomorrow</h2>
        <Tomorrow data={data} />
      </section>

      <section className="card">
        <h2>Up next</h2>
        {data.queued.length === 0 ? (
          <p className="muted">nothing queued</p>
        ) : (
          <ol className="advisor-queue">
            {data.queued.map((q) => (
              <li key={q.id}>
                <strong>{q.name ?? q.id}</strong> <span className="muted">· {stub(q.hypothesis, 140)}</span>
              </li>
            ))}
          </ol>
        )}
        <p className="muted">
          A queued experiment activates at an evening pass once the module has a free slot; with no cap declared,
          every queued experiment activates and runs on its own advised book.
        </p>
      </section>

      <section className="card">
        <h2>Recently concluded</h2>
        <ConcludedList items={data.concluded} />
      </section>
    </div>
  );
}

export function AdvisorSlide({ module }: { module: string }) {
  const { data, isLoading, isError } = useAdvisorModule(module);
  if (isLoading) return <span className="skeleton skeleton-text" style={{ width: "50%" }} />;
  if (isError || data === undefined)
    return (
      <section className="card">
        <p className="pnl-neg">could not read the advisor store</p>
      </section>
    );
  return <AdvisorSlideBody data={data} />;
}
