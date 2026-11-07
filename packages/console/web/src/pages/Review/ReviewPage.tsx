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

/**
 * The suite review. Renders the fact set and computes nothing.
 *
 * Every figure comes from `data/review/eod-<day>.json`, the artifact `packages/review` writes — the
 * markdown render, this page and the narrative all read that one file, so they cannot hold different
 * opinions about a session.
 *
 * Built from the console's own primitives (`card`, `stats-grid`/`stat-tile`, `data-table`, `chip`,
 * `dot`) rather than a parallel look. The first version of this page invented class names that did
 * not exist — `.page-head`, `.data`, `.findings` — so half of it rendered unstyled, which is most of
 * why it read flat.
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

interface Flag {
  level: "warn" | "err";
  text: string;
}

/** What a reader needs before anything else. An empty list is a real and good answer. */
function attention(modules: ReviewModule[]): Flag[] {
  const out: Flag[] = [];
  for (const m of modules) {
    if (!m.ok) {
      out.push({ level: "err", text: `${m.module} could not be read — ${m.reason}` });
      continue;
    }
    if (m.loopTicked === false) {
      out.push({ level: "err", text: `${m.module} did not tick at all — a stopped loop, not a quiet day` });
    }
    if (m.errors) out.push({ level: "err", text: `${m.module} logged ${m.errors} iteration error(s)` });
    if (m.suspectedBreak) {
      out.push({
        level: "warn",
        text:
          `${m.module} looks like a regime change with no journaled break — ${m.suspectedBreak.trades} trades ` +
          `against a trailing median of ${Math.round(m.suspectedBreak.trailingMedian)} (${m.suspectedBreak.ratio}×)`,
      });
    }
    if (m.breaks === null) {
      out.push({
        level: "warn",
        text: `${m.module} does not track measurement breaks — its trend assumes a continuity nothing verified`,
      });
    }
  }
  return out;
}

/** Arms that used one centring rule all session are that rule's arm, not independent observations. */
function collapsedArms(m: ReviewModule): string[] {
  const byRule = new Map<string, string[]>();
  for (const a of m.arms) {
    if (!a.centredBy) continue;
    byRule.set(a.centredBy, [...(byRule.get(a.centredBy) ?? []), a.arm]);
  }
  return [...byRule.entries()]
    .filter(([, arms]) => arms.length > 1)
    .map(([rule, arms]) => `${arms.slice().sort().join(", ")} all centred ${rule}`);
}

/**
 * A proportion bar behind an arm's net. Deliberately scaled to the largest ABSOLUTE net in the
 * group, so a loss and a win of the same size read as the same weight in opposite colours — the
 * alternative scales losses against wins and makes a bad arm look small.
 */
function ArmBar({ net, peak }: { net: number; peak: number }) {
  return <SignedBar value={net} maxAbs={peak} compact />;
}

function ModuleCard({ m }: { m: ReviewModule }) {
  if (!m.ok) {
    return (
      <Card title={m.module} className="review-module" controls={<span className="chip chip-missing">unreadable</span>}>
        <p className="muted">{m.reason}</p>
      </Card>
    );
  }
  const peak = m.arms.reduce((max, a) => Math.max(max, Math.abs(a.net)), 0);

  return (
    <Card
      title={m.module}
      className="review-module"
      help={MODULE_HELP}
      controls={
        <>
          {m.closed === 0 && <span className="chip">no trades</span>}
          {m.singleEvent && m.closed > 0 && (
            <span className="chip chip-warn" title="Trades sharing a symbol and session share one market event">
              {count(m.events)} event
            </span>
          )}
          <span className="card-asof">
            {count(m.n)} row{m.n === 1 ? "" : "s"}
          </span>
        </>
      }
    >

      <div className="stats-grid">
        <div className="stat-tile">
          <span className="stat-label">Net</span>
          <span className={`stat-value ${pnlClass(m.net)}`}>{money(m.net)}</span>
        </div>
        <div className="stat-tile">
          <span className="stat-label">Return on risk</span>
          <span className={`stat-value ${pnlClass(m.onMaxRisk)}`}>{pct(m.onMaxRisk)}</span>
        </div>
        <div className="stat-tile">
          <span className="stat-label">Closed</span>
          <span className="stat-value">{count(m.closed)}</span>
        </div>
        <div className="stat-tile">
          <span className="stat-label">Won / lost</span>
          <span className="stat-value">
            {count(m.wins)} / {count(m.closed - m.wins)}
          </span>
        </div>
        <div className="stat-tile">
          <span className="stat-label">Capital at risk</span>
          <span className="stat-value">{money(m.capitalAtRisk)}</span>
        </div>
      </div>

      {m.arms.length > 1 && (
        <>
          <div className="review-subhead">By arm</div>
          <div className="table-scroll">
          <table className="data-table data-table-labelled">
            <thead>
              <tr>
                <th>Arm</th>
                <th>Centred by</th>
                <th />
                <th>Net</th>
                <th>Return</th>
                <th>Closed</th>
                <th>Wins</th>
              </tr>
            </thead>
            <tbody>
              {m.arms.map((a: ReviewArm) => (
                <tr key={a.arm}>
                  <td>{a.arm}</td>
                  <td className="muted">{a.centredBy ?? "—"}</td>
                  <td className="arm-bar-cell">
                    <ArmBar net={a.net} peak={peak} />
                  </td>
                  <td className={pnlClass(a.net)}>{money(a.net)}</td>
                  <td className={pnlClass(a.onMaxRisk)}>{pct(a.onMaxRisk)}</td>
                  <td>{count(a.closed)}</td>
                  <td>{count(a.wins)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          </div>
          {collapsedArms(m).map((c) => (
            <p className="review-caveat" key={c}>
              <span className="dot status-warn" /> {c} — not independent this session. A GEX-centred arm
              degrades to ATM when no open interest is cached, at which point it is the control arm under
              another name.
            </p>
          ))}
        </>
      )}

      <div className="review-expected">
        <span className="stat-label">Expected vs observed</span>
        <span className="muted"> · {m.expectedBasis ?? "no model"}</span>
        <div>
          {m.expected === null && m.observed === null ? (
            <span className="muted">nothing to compare this session</span>
          ) : (
            <span className="review-expected-figures">
              {money(m.expected)} <span className="muted">expected</span> → {money(m.observed)}{" "}
              <span className="muted">observed</span>
              {m.expected === null && <span className="muted"> (no model recorded)</span>}
            </span>
          )}
        </div>
      </div>

      {m.carriedPositions > 0 && (
        <p className="review-caveat">
          <span className="dot status-warn" /> {m.carriedPositions} position(s) carried overnight,{" "}
          {money(m.carriedCapital)} at risk
          {m.carriedCapitalUnknown > 0 ? ` (+${m.carriedCapitalUnknown} of unknown capital)` : ""} — no realised P&amp;L until they settle.
        </p>
      )}
    </Card>
  );
}

const MODULE_HELP: CardHelpContent = {
  title: "Reading a module's session",
  body: (
    <HelpSection title="By arm">
      <p>
        The arms traded the same underlying in the same session, so the table is a paired comparison. An arm's bar is scaled to the
        largest absolute net in the group, so a loss and a win of the same size read as the same weight in opposite colours. Return is on
        the maximum risk.
      </p>
    </HelpSection>
  ),
};

const ATTENTION_HELP: CardHelpContent = {
  title: "What this review is",
  body: (
    <>
      <HelpSection title="Where the figures come from">
        <p>
          Every figure is read from the fact set the review package writes after the close; the page computes nothing. An unmeasured value
          is a dash, never 0.00.
        </p>
      </HelpSection>
      <HelpSection title="No suite total">
        <p>
          Deliberately: these books differ in scale by more than an order of magnitude, so a combined figure would describe the largest one
          and imply it described all three.
        </p>
      </HelpSection>
    </>
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

  const current = data?.current ?? null;
  const modules = current?.modules ?? [];
  const flags = attention(modules);
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

  return (
    <div className="page">
      <div className="page-title-row">
        <h1>Suite review</h1>
        {tabs}
        {current && (
          <span className={`chip ${current.status === "final" ? "chip-ok" : "chip-warn"}`}>{current.status}</span>
        )}
        {current && <span className="chip">paper</span>}
        {current && <span className="chip">{current.session}</span>}
        {current && <span className="card-asof">fact set v{current.factVersion}</span>}
      </div>

      {isError && <p className="muted">Could not read the review store.</p>}
      {isLoading && !current && <p className="muted">Reading the review store…</p>}
      {!isLoading && !isError && !current && (
        <p className="muted">No fact set has been written yet. The review runs after the close.</p>
      )}

      {current && (
        <div className="pf-layout">
          <Section concept="stand" tag="the session" question="What needs my attention?">
          <Card
            title="Needs attention"
            className={`review-attention ${flags.length > 0 ? "review-attention-flagged" : ""}`}
            help={ATTENTION_HELP}
            controls={
              <>
                {flags.length === 0 && <span className="chip chip-ok">clear</span>}
                {flags.length > 0 && <span className="chip chip-warn">{flags.length}</span>}
                {data && data.sessions.length > 1 ? sessionFilter.button : null}
              </>
            }
          >
            {data && data.sessions.length > 1 ? sessionFilter.body : null}
            {flags.length === 0 ? (
              <p className="muted">
                Every module ticked, none errored, and no unjournaled regime change.
              </p>
            ) : (
              <ul className="review-flags">
                {flags.map((f) => (
                  <li key={f.text}>
                    <span className={`dot ${f.level === "err" ? "status-err" : "status-warn"}`} />
                    {f.text}
                  </li>
                ))}
              </ul>
            )}
            {current.status === "provisional" && (
              <p className="review-caveat">
                <span className="dot status-warn" /> Provisional — the overnight module has not settled, so
                its realised P&amp;L lands in the next session.
              </p>
            )}
          </Card>
          </Section>

          <Section concept="hist" tag="by module" question="How did each module do?">
            {modules.map((m) => (
              <ModuleCard key={m.module} m={m} />
            ))}
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
            <span className="card-asof" title={data.era.eraNote ?? undefined}>
              {data.era.eraFrom !== null && <>era from {data.era.eraFrom} · </>}
              {data.era.sessions} sessions{data.era.from !== null && <> · {data.era.from} → {data.era.to}</>}
            </span>
          }
        >
          <div className="stats-grid">
            {Object.keys(data.era.netByModule).map((name) => {
              // Small multiples: the same shape per module, read side by side. The line is the
              // running total of the sessions summed into the tile above it — one pass, one story.
              const trend = data.era.trendByModule[name] ?? [];
              return (
                <div className="stat-tile" key={name}>
                  <span className="stat-label">{name}</span>
                  <span className={`stat-value ${pnlClass(data.era.netByModule[name])}`}>
                    {compactMoney(data.era.netByModule[name])}
                  </span>
                  <Sparkline
                    values={trend.map((t) => t.net)}
                    title={`${name}: cumulative net across ${trend.length} session${trend.length === 1 ? "" : "s"}`}
                  />
                  <span className="stat-label">
                    {count(data.era.closedByModule[name])} closed
                    {trend.length === 1 && <span className="muted"> · 1 session</span>}
                  </span>
                </div>
              );
            })}
          </div>
        </Card>
        </Section>
        </div>
      )}
    </div>
  );
}
