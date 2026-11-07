import { Fragment } from "react";
import { useQuery } from "@tanstack/react-query";
import type {
  AdvisorActiveExperiment,
  AdvisorApplyStatus,
  AdvisorExperiment,
  AdvisorModulePayload,
  AdvisorSessionCell,
} from "@console/shared";
import { PairTable } from "../../pages/Advisor/AdvisorPage";
import { Card } from "../DataTable";
import { Section } from "../Section";
import { HelpSection, HelpWarn, type CardHelpContent } from "../CardHelp";
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

export function useAdvisorModule(module: string) {
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

export const STATUS_LABEL: Record<string, string> = {
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
    <div className="table-scroll">
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
    </div>
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
                  <ParamName name={p.param} /> = {String(p.value)}
                </li>
              ))}
            </ul>
          )}
        </div>
      ))}
    </>
  );
}

/** A dotted param path that may break after each dot: "directional_credit_spread.close_losers_first_morning"
 *  is one unbreakable word otherwise, and ran a phone-width card 80px past its edge. */
export function ParamName({ name }: { name: string }) {
  const parts = name.split(".");
  return (
    <>
      {parts.map((part, i) => (
        <Fragment key={i}>
          {i > 0 && (
            <>
              .<wbr />
            </>
          )}
          {part}
        </Fragment>
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
            {e.verdict?.noExposure != null && " · no exposure, measured nothing"}
            {e.verdict?.noExposure == null && e.verdict?.underpowered === true && " · underpowered"}
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

const RUNNING_HELP: CardHelpContent = {
  title: "A running experiment",
  body: (
    <>
      <HelpSection title="The two bars">
        <p>
          The first bar is the sessions the module actually applied the advice, over the experiment&apos;s length. The thin one
          is calendar sessions since it started, against the stall budget (twice the length).
        </p>
      </HelpSection>
      <HelpSection title="The overlay">
        <p>The params the advisor admitted, run on the experiment&apos;s own advised arm against its base.</p>
      </HelpSection>
    </>
  ),
};

const VERDICT_HELP: CardHelpContent = {
  title: "The comparison",
  body: (
    <HelpSection title="As of the last evening pass">
      <p>
        The advised arm against its base, as the advisor scored it at its last evening pass, never recomputed here. The gate is
        what must be met before the comparison counts as measured.
      </p>
    </HelpSection>
  ),
};

const SESSIONS_HELP: CardHelpContent = {
  title: "Session by session",
  body: (
    <>
      <HelpSection title="What counts">
        <p>
          Only an applied session advances the count. Carried means the params were already frozen on positions the module
          still held; nothing new was decided and nothing was charged.
        </p>
      </HelpSection>
      <HelpSection title="One strip per experiment">
        <p>Where several experiments run at once, each is scored on its own arm and has its own strip.</p>
      </HelpSection>
    </>
  ),
};

const QUEUE_HELP: CardHelpContent = {
  title: "Up next",
  body: (
    <HelpSection title="When a queued experiment starts">
      <p>
        A queued experiment activates at an evening pass once the module has a free slot. With no cap declared, every queued
        experiment activates and runs on its own advised arm.
      </p>
    </HelpSection>
  ),
};

const TOMORROW_HELP: CardHelpContent = {
  title: "Tomorrow",
  body: (
    <>
      <HelpWarn title="Advice can be off">
        <p>
          When advice is off, or no artifact was written, the module runs its baseline. An admitted param overlays the base for
          the next session only.
        </p>
      </HelpWarn>
    </>
  ),
};

const CONCLUDED_HELP: CardHelpContent = {
  title: "Recently concluded",
  body: (
    <HelpSection title="Verdicts">
      <p>
        The advisor&apos;s own status for each, and the model&apos;s recommendation where it gave one. Stalled, underpowered
        and no-exposure are the advisor&apos;s stamps, not this page&apos;s.
      </p>
    </HelpSection>
  ),
};

function RunningExperiment({ e, module }: { e: AdvisorActiveExperiment; module: string }) {
  return (
    <Card
      title={
        <>
          Running on {module}
          <span className="muted"> · {e.name ?? e.id}</span>
        </>
      }
      collapseKey={`advisor-${module}-running-${e.id}`}
      help={RUNNING_HELP}
    >
      <p className="muted">
        {e.tag ?? `advised:${e.baseProfile}`} against {e.baseProfile}
      </p>
      <p className="muted">{stub(e.hypothesis)}</p>
      <ParamRows params={e.params} />
      <Progress e={e} />
    </Card>
  );
}

/** The slide's body, pure over its payload so it can be rendered and asserted without a client. */
export function AdvisorSlideBody({ data }: { data: AdvisorModulePayload }) {
  if (!data.storePresent) {
    return (
      <div className="pf-layout">
        <Card title="Advisor" collapseKey={`advisor-${data.module}-none`}>
          <p className="muted">the advisor has not run yet — nothing to show for {data.module}</p>
        </Card>
      </div>
    );
  }
  const active = data.active;
  return (
    <div className="pf-layout">
      <Section concept="stand" tag="the experiment" question="What is running, and is it working?">
        {active.length === 0 ? (
          <Card title={`Running on ${data.module}`} collapseKey={`advisor-${data.module}-running`}>
            <p className="muted">
              no active experiment{data.queued.length > 0 ? ` — ${data.queued.length} queued, activates at the next evening pass` : ""}
            </p>
          </Card>
        ) : (
          active.map((e) => <RunningExperiment key={e.id} e={e} module={data.module} />)
        )}

        {active
          .filter((e) => e.verdict !== null)
          .map((e) => (
            <Card
              key={`verdict-${e.id}`}
              title={
                <>
                  {e.name ?? e.id} against {e.baseProfile}
                  <span className="muted"> · as of the last evening pass</span>
                </>
              }
              collapseKey={`advisor-${data.module}-verdict-${e.id}`}
              help={VERDICT_HELP}
            >
              <PairTable pairs={e.verdict!.pairs} />
              {(() => {
                const d = e.verdict!.pairs[0]?.delta;
                const gate = gateDistance(e);
                return (
                  <p className="muted">
                    {d !== undefined && <>net delta {money(d["net_pnl"])}</>}
                    {gate !== null && <> · gate: {gate}</>}
                    {e.verdict!.noExposure != null && <> · no exposure: {e.verdict!.noExposure.detail}</>}
                    {e.verdict!.noExposure == null && e.verdict!.underpowered && <> · below the gate, so not yet measured</>}
                  </p>
                );
              })()}
            </Card>
          ))}
      </Section>

      <Section concept="hist" tag="the record" question="Which sessions did the advice apply to?">
        <Card title="Session by session" collapseKey={`advisor-${data.module}-sessions`} help={SESSIONS_HELP}>
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
        </Card>
      </Section>

      <Section concept="room" tag="next" question="What does the module run next?">
        <Card title="Tomorrow" collapseKey={`advisor-${data.module}-tomorrow`} help={TOMORROW_HELP}>
          <Tomorrow data={data} />
        </Card>

        <Card title="Up next" collapseKey={`advisor-${data.module}-queue`} help={QUEUE_HELP}>
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
        </Card>
      </Section>

      <Section concept="edge" tag="concluded" question="What has it already settled?">
        <Card title="Recently concluded" collapseKey={`advisor-${data.module}-concluded`} help={CONCLUDED_HELP}>
          <ConcludedList items={data.concluded} />
        </Card>
      </Section>
    </div>
  );
}

export function AdvisorSlide({ module }: { module: string }) {
  const { data, isLoading, isError } = useAdvisorModule(module);
  if (isLoading) return <span className="skeleton skeleton-text" style={{ width: "50%" }} />;
  if (isError || data === undefined)
    return (
      <Card title="Advisor" isError collapseKey={`advisor-${module}-error`}>
        <p className="pnl-neg">could not read the advisor store</p>
      </Card>
    );
  return <AdvisorSlideBody data={data} />;
}
