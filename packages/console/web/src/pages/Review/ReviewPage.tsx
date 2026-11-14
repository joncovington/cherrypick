import { useState, type ReactNode } from "react";
import { useReview } from "../../lib/api";
import type { ReviewArm, ReviewModule } from "@console/shared";
import { NoteMarkdown } from "./NoteMarkdown";
import { useFeatures } from "../../lib/useFeatures";
import { isFeatureOn } from "../../lib/visibility";
import { SignedBar, Sparkline } from "../../components/Charts";
import { Card } from "../../components/DataTable";
import { HelpSection, type CardHelpContent } from "../../components/CardHelp";
import { FilterGroup } from "../../components/FilterSort";
import { Section } from "../../components/Section";
import { useFilterPanel } from "../useFilterPanel";
import { attention, collapsedArms, moduleAnchor, shownArms, splitModules, type Flag } from "./summary";

/**
 * The suite review. Renders the fact set and computes nothing.
 *
 * Every figure comes from `data/review/eod-<day>.json`, the artifact `packages/review` writes — the
 * markdown render, this page and the narrative all read that one file, so they cannot hold different
 * opinions about a session.
 *
 * One summary table, one row a module that traded (or carried a position, or could not be read);
 * a row opens to its arms, top three by net and then "n more". Modules with no trades share one
 * line under the table. What needs attention comes first, each line linking to its module's row.
 * The era's totals sit last, folded to one line until opened.
 *
 * Two rules the styling has to carry, not just the numbers:
 * - **Null is not zero.** An unmeasured value renders as an em dash, never 0.00. A cost never
 *   recorded and a cost of zero are different facts.
 * - **Weight follows evidence.** The accent is reserved for things that need attention. A one-session
 *   trend is not dressed to look like a result.
 */

function money(v: number | null | undefined): string {
  if (v === null || v === undefined) return "—";
  return v.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function compactMoney(v: number | null | undefined): string {
  if (v === null || v === undefined) return "—";
  const sign = v < 0 ? "−" : "";
  const abs = Math.abs(v);
  if (abs >= 1000) return `${sign}$${(abs / 1000).toFixed(1)}k`;
  return `${sign}$${abs.toFixed(0)}`;
}

function pct(v: number | null | undefined): string {
  return v === null || v === undefined ? "—" : `${(v * 100).toFixed(1)}%`;
}

function count(v: number | null | undefined): string {
  return v === null || v === undefined ? "—" : v.toLocaleString();
}

function pnlClass(v: number | null | undefined): string {
  if (v === null || v === undefined || v === 0) return "";
  return v > 0 ? "pnl-pos" : "pnl-neg";
}

/** The row's short note: expected against observed, and the flags a reader should not miss. */
function rowNote(m: ReviewModule): ReactNode {
  const parts: ReactNode[] = [];
  if (m.expected !== null || m.observed !== null) {
    parts.push(
      <span key="eo">
        {money(m.expected)} <span className="muted">exp</span> → {money(m.observed)} <span className="muted">obs</span>
      </span>,
    );
  }
  if (m.singleEvent && m.closed > 0) {
    parts.push(
      <span key="ev" className="chip chip-warn" title="Trades sharing a symbol and session share one market event">
        {count(m.events)} event
      </span>,
    );
  }
  if (m.carriedPositions > 0) parts.push(<span key="cr" className="chip">{m.carriedPositions} carried</span>);
  return parts.length === 0 ? <span className="muted">—</span> : <span className="rv-note-parts">{parts}</span>;
}

function ArmsTable({ m }: { m: ReviewModule }) {
  const [all, setAll] = useState(false);
  const { shown, more } = shownArms(m.arms, all);
  // Scaled to the largest ABSOLUTE net among all the arms, shown or not, so a loss and a win of the
  // same size read as the same weight in opposite colours, and opening "more" does not rescale.
  const peak = m.arms.reduce((max, a) => Math.max(max, Math.abs(a.net)), 0);
  return (
    <>
      <div className="table-scroll">
        <table className="data-table rv-arms">
          <thead>
            <tr>
              <th>Arm</th>
              <th className="rv-share">Share of net</th>
              <th>Net</th>
              <th className="rv-ret">Return</th>
              <th>Closed</th>
              <th>Wins</th>
            </tr>
          </thead>
          <tbody>
            {shown.map((a: ReviewArm) => (
              <tr key={a.arm}>
                <td className="rv-arm" title={a.centredBy ? `${a.arm} · centred ${a.centredBy}` : a.arm}>
                  {a.arm}
                </td>
                <td className="rv-share arm-bar-cell">
                  <SignedBar value={a.net} maxAbs={peak} compact />
                </td>
                <td className={pnlClass(a.net)}>{money(a.net)}</td>
                <td className={`rv-ret ${pnlClass(a.onMaxRisk)}`}>{pct(a.onMaxRisk)}</td>
                <td>{count(a.closed)}</td>
                <td>{count(a.wins)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {(more > 0 || all) && m.arms.length > 3 && (
        <button type="button" className="rv-link" onClick={() => setAll(!all)}>
          {all ? "show top 3" : `${String(more)} more`}
        </button>
      )}
    </>
  );
}

function ModuleDetail({ m }: { m: ReviewModule }) {
  if (!m.ok) return <p className="muted">{m.reason}</p>;
  return (
    <div className="rv-detail">
      {m.arms.length > 1 && <ArmsTable m={m} />}
      {collapsedArms(m).map((c) => (
        <p className="review-caveat" key={c}>
          <span className="dot status-warn" /> {c} — not independent this session. A GEX-centred arm degrades to ATM when no open interest
          is cached, at which point it is the control arm under another name.
        </p>
      ))}
      <p className="rv-line">
        <span className="stat-label">Expected vs observed</span>
        <span className="muted"> · {m.expectedBasis ?? "no model"} · </span>
        {m.expected === null && m.observed === null ? (
          <span className="muted">nothing to compare this session</span>
        ) : (
          <>
            {money(m.expected)} <span className="muted">expected</span> → {money(m.observed)} <span className="muted">observed</span>
            {m.expected === null && <span className="muted"> (no model recorded)</span>}
          </>
        )}
      </p>
      {m.carriedPositions > 0 && (
        <p className="review-caveat">
          <span className="dot status-warn" /> {m.carriedPositions} position(s) carried overnight, {money(m.carriedCapital)} at risk
          {m.carriedCapitalUnknown > 0 ? ` (+${String(m.carriedCapitalUnknown)} of unknown capital)` : ""} — no realised P&amp;L until they
          settle.
        </p>
      )}
    </div>
  );
}

function ModuleRows({ m, open, onToggle }: { m: ReviewModule; open: boolean; onToggle: () => void }) {
  return (
    <>
      <tr id={moduleAnchor(m.module)} className={open ? "rv-row is-open" : "rv-row"}>
        <td>
          <button type="button" className="rv-toggle" aria-expanded={open} onClick={onToggle}>
            <span className="rv-caret" aria-hidden>
              {open ? "▾" : "▸"}
            </span>
            {m.module}
          </button>
        </td>
        {m.ok ? (
          <>
            <td className={pnlClass(m.net)}>{money(m.net)}</td>
            <td className={`rv-ret ${pnlClass(m.onMaxRisk)}`}>{pct(m.onMaxRisk)}</td>
            <td className="rv-car muted">{money(m.capitalAtRisk)}</td>
            <td>{count(m.closed)}</td>
            <td>
              {count(m.wins)} / {count(m.closed - m.wins)}
            </td>
            <td className="rv-note">{rowNote(m)}</td>
          </>
        ) : (
          <td colSpan={6}>
            <span className="chip chip-missing">unreadable</span>
          </td>
        )}
      </tr>
      {open && (
        <tr className="rv-open">
          <td colSpan={7}>
            <ModuleDetail m={m} />
          </td>
        </tr>
      )}
    </>
  );
}

function FlagLine({ f, linkable, onJump }: { f: Flag; linkable: boolean; onJump: (module: string) => void }) {
  return (
    <li>
      <span className={`dot ${f.level === "err" ? "status-err" : "status-warn"}`} />
      <span>
        {f.module !== null &&
          (linkable ? (
            <button type="button" className="rv-jump" onClick={() => onJump(f.module!)}>
              {f.module}
            </button>
          ) : (
            <strong>{f.module}</strong>
          ))}
        {f.module !== null ? " " : ""}
        {f.text}
      </span>
    </li>
  );
}

const SUMMARY_HELP: CardHelpContent = {
  title: "Reading the summary",
  body: (
    <>
      <HelpSection title="Rows">
        <p>
          One row a module that closed a trade, carried a position or could not be read; the rest share the line beneath. Open a row for its
          arms, largest net first. The arms traded the same underlying in the same session, so they are a paired comparison. An arm's bar is
          scaled to the largest absolute net among them, so a loss and a win of the same size read as the same weight. Return is on the
          maximum risk.
        </p>
      </HelpSection>
      <HelpSection title="No suite total">
        <p>
          Deliberately: these books differ in scale by more than an order of magnitude, so a combined figure would describe the largest one
          and imply it described all of them.
        </p>
      </HelpSection>
    </>
  ),
};

const ATTENTION_HELP: CardHelpContent = {
  title: "What this review is",
  body: (
    <HelpSection title="Where the figures come from">
      <p>
        Every figure is read from the fact set the review package writes after the close; the page computes nothing. An unmeasured value is
        a dash, never 0.00.
      </p>
    </HelpSection>
  ),
};

const NOTE_HELP: CardHelpContent = {
  title: "About the note",
  body: (
    <HelpSection title="Interpretation, not fact">
      <p>The note is written from the fact set above it; the figures are the fact set's, the reading of them is the note's.</p>
    </HelpSection>
  ),
};

const ERA_HELP: CardHelpContent = {
  title: "The era's totals",
  body: (
    <HelpSection title="Where they come from">
      <p>
        Summed from the built fact sets, not a fresh pass over the ledgers, so it cannot disagree with the sessions above and its depth is
        exactly what has been built. Each line is that module's cumulative net across the era's sessions; a module with one session gets no
        line, because one point is not a trend.
      </p>
    </HelpSection>
  ),
};

/** `tabs` is the Reports page's tab strip, rendered inside this page's own title row. Optional so
 *  the page still stands alone if it is ever routed to directly. */
export function ReviewPage({ tabs }: { tabs?: ReactNode } = {}) {
  const [session, setSession] = useState<string | undefined>(undefined);
  const { data, isLoading, isError } = useReview(session);
  // The written note leaves the page when the suite turns the review narrative off; the fact set
  // is the review package's and always shows.
  const showNarrative = isFeatureOn("review_narrative", useFeatures());
  // Rows the reader opened; null until they touch one, which leaves the first row open.
  const [opened, setOpened] = useState<Set<string> | null>(null);
  const [eraOpen, setEraOpen] = useState(false);

  const current = data?.current ?? null;
  const modules = current?.modules ?? [];
  const flags = attention(modules, current?.status ?? null);
  const { rows, idle } = splitModules(modules);
  const openSet = opened ?? new Set(rows.length > 0 ? [rows[0]!.module] : []);
  const toggle = (module: string) => {
    const next = new Set(openSet);
    if (next.has(module)) next.delete(module);
    else next.add(module);
    setOpened(next);
  };
  const jump = (module: string) => {
    const next = new Set(openSet);
    next.add(module);
    setOpened(next);
    requestAnimationFrame(() => document.getElementById(moduleAnchor(module))?.scrollIntoView({ block: "start", behavior: "smooth" }));
  };
  const closedTotal = modules.reduce((n, m) => n + (m.ok ? m.closed : 0), 0);
  const latest = data?.sessions[data.sessions.length - 1];
  const sessionFilter = useFilterPanel({
    parts: [session === undefined || session === latest ? "latest session" : `session ${session}`],
    changed: session !== undefined && session !== latest ? 1 : 0,
    reset: () => setSession(undefined),
    children: (
      <FilterGroup title="session" note="the fact set the review wrote that night">
        <select className="chip review-session-select" value={current?.session ?? ""} onChange={(e) => setSession(e.target.value)} aria-label="Session">
          {(data?.sessions ?? [])
            .slice()
            .reverse()
            .map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
        </select>
      </FilterGroup>
    ),
  });
  const pickSession = data !== undefined && data.sessions.length > 1;
  const rowModules = new Set(rows.map((m) => m.module));

  return (
    <div className="page">
      <div className="page-title-row">
        <h1>Suite review</h1>
        {tabs}
        {current && <span className={`chip ${current.status === "final" ? "chip-ok" : "chip-warn"}`}>{current.status}</span>}
        {current && <span className="chip">paper</span>}
        {current && <span className="chip">{current.session}</span>}
        {current && (
          <span className="card-asof rv-meta">
            fact set v{current.factVersion} · {count(closedTotal)} closed · {modules.length} modules read
          </span>
        )}
        {pickSession ? <span className="rv-session">{sessionFilter.button}</span> : null}
      </div>
      {pickSession ? sessionFilter.body : null}

      {isError && <p className="muted">Could not read the review store.</p>}
      {isLoading && !current && <p className="muted">Reading the review store…</p>}
      {!isLoading && !isError && !current && <p className="muted">No fact set has been written yet. The review runs after the close.</p>}

      {current && (
        <div className="pf-layout">
          <Section concept="stand" tag="the session" question="What needs my attention?">
            <Card
              title="Needs attention"
              className={`review-attention ${flags.length > 0 ? "review-attention-flagged" : ""}`}
              help={ATTENTION_HELP}
              controls={flags.length === 0 ? <span className="chip chip-ok">clear</span> : <span className="chip chip-warn">{flags.length}</span>}
            >
              {flags.length === 0 ? (
                <p className="muted">Every module ticked, none errored, and no unjournaled regime change.</p>
              ) : (
                <ul className="review-flags rv-flags">
                  {flags.map((f) => (
                    <FlagLine key={`${f.module ?? ""}${f.text}`} f={f} linkable={f.module !== null && rowModules.has(f.module)} onJump={jump} />
                  ))}
                </ul>
              )}
            </Card>
          </Section>

          <Section concept="hist" tag="by module" question="How did each module do?">
            <Card title="Modules" help={SUMMARY_HELP} className="rv-summary">
              {rows.length === 0 ? (
                <p className="muted">No module closed a trade or carried a position this session.</p>
              ) : (
                <div className="table-scroll">
                  <table className="data-table rv-table">
                    <thead>
                      <tr>
                        <th>Module</th>
                        <th>Net</th>
                        <th className="rv-ret">Return on risk</th>
                        <th className="rv-car">Capital at risk</th>
                        <th>Closed</th>
                        <th>Won / lost</th>
                        <th className="rv-note">Expected vs observed · flags</th>
                      </tr>
                    </thead>
                    <tbody>
                      {rows.map((m) => (
                        <ModuleRows key={m.module} m={m} open={openSet.has(m.module)} onToggle={() => toggle(m.module)} />
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
              {idle.length > 0 && (
                <p className="rv-idle">
                  <span className="muted">No trades:</span>{" "}
                  {idle.map((i, n) => (
                    <span key={i.module}>
                      {n > 0 && " · "}
                      <strong>{i.module}</strong>
                      {i.note && <span className="muted"> ({i.note})</span>}
                    </span>
                  ))}
                </p>
              )}
            </Card>
          </Section>

          {showNarrative && current.note && (
            <Section concept="edge" tag="interpretation" question="What does the note say?">
              <Card title="Note" className="review-note" help={NOTE_HELP}>
                <NoteMarkdown text={current.note} />
              </Card>
            </Section>
          )}
        </div>
      )}

      {data && data.era.sessions > 0 && (
        <div className="pf-layout">
          <Section concept="hist" tag="the era" question="What has the era done?">
            <Card
              title={data.era.eraFrom !== null ? "This era" : "All time"}
              help={ERA_HELP}
              controls={
                <>
                  <span className="card-asof" title={data.era.eraNote ?? undefined}>
                    {data.era.eraFrom !== null && <>era from {data.era.eraFrom} · </>}
                    {data.era.sessions} sessions
                    {data.era.from !== null && (
                      <>
                        {" "}
                        · {data.era.from} → {data.era.to}
                      </>
                    )}
                  </span>
                  <button type="button" className="chip rv-era-toggle" aria-expanded={eraOpen} onClick={() => setEraOpen(!eraOpen)}>
                    {eraOpen ? "hide" : "show"}
                  </button>
                </>
              }
            >
              {!eraOpen ? (
                <p className="rv-era-line">
                  {Object.keys(data.era.netByModule).map((name, n) => (
                    <span key={name}>
                      {n > 0 && " · "}
                      {name} <span className={pnlClass(data.era.netByModule[name])}>{compactMoney(data.era.netByModule[name])}</span>
                    </span>
                  ))}
                </p>
              ) : (
                <div className="stats-grid">
                  {Object.keys(data.era.netByModule).map((name) => {
                    // Small multiples: the same shape per module, read side by side. The line is the
                    // running total of the sessions summed into the tile above it — one pass, one story.
                    const trend = data.era.trendByModule[name] ?? [];
                    return (
                      <div className="stat-tile" key={name}>
                        <span className="stat-label">{name}</span>
                        <span className={`stat-value ${pnlClass(data.era.netByModule[name])}`}>{compactMoney(data.era.netByModule[name])}</span>
                        <Sparkline
                          values={trend.map((t) => t.net)}
                          title={`${name}: cumulative net across ${String(trend.length)} session${trend.length === 1 ? "" : "s"}`}
                        />
                        <span className="stat-label">
                          {count(data.era.closedByModule[name])} closed
                          {trend.length === 1 && <span className="muted"> · 1 session</span>}
                        </span>
                      </div>
                    );
                  })}
                </div>
              )}
            </Card>
          </Section>
        </div>
      )}
    </div>
  );
}
