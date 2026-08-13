import { useState } from "react";
import { useReview } from "../../lib/api";
import type { ReviewArm, ReviewModule } from "@console/shared";
import { NoteMarkdown } from "./NoteMarkdown";

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
  const width = peak > 0 ? Math.max(2, (Math.abs(net) / peak) * 100) : 0;
  return (
    <span className="arm-bar" aria-hidden>
      <span className={`arm-bar-fill ${net < 0 ? "arm-bar-neg" : "arm-bar-pos"}`} style={{ width: `${width}%` }} />
    </span>
  );
}

function ModuleCard({ m }: { m: ReviewModule }) {
  if (!m.ok) {
    return (
      <section className="card review-module">
        <div className="card-head">
          <h2>{m.module}</h2>
          <span className="chip chip-missing">unreadable</span>
        </div>
        <p className="muted">{m.reason}</p>
      </section>
    );
  }
  const peak = m.arms.reduce((max, a) => Math.max(max, Math.abs(a.net)), 0);
  const thin = (m.effectiveN ?? 0) <= 1;

  return (
    <section className="card review-module">
      <div className="card-head">
        <h2>{m.module}</h2>
        {m.closed === 0 && <span className="chip">no trades</span>}
        {thin && m.closed > 0 && (
          <span className="chip chip-warn" title="Trades sharing a symbol and session share one market event">
            {count(m.effectiveN)} event
          </span>
        )}
        <span className="card-asof">
          {count(m.n)} row{m.n === 1 ? "" : "s"}
        </span>
      </div>

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
          <div className="review-subhead">
            By arm
            <span className="muted"> · same underlying, same session — a paired comparison</span>
          </div>
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
          {money(m.carriedCapital)} at risk — no realised P&amp;L until they settle.
        </p>
      )}
    </section>
  );
}

export function ReviewPage() {
  const [session, setSession] = useState<string | undefined>(undefined);
  const { data, isLoading, isError } = useReview(session);

  const current = data?.current ?? null;
  const modules = current?.modules ?? [];
  const flags = attention(modules);

  return (
    <div className="page">
      <div className="page-title-row">
        <h1>Suite review</h1>
        {current && (
          <span className={`chip ${current.status === "final" ? "chip-ok" : "chip-warn"}`}>{current.status}</span>
        )}
        {current && <span className="chip">paper</span>}
        {data && data.sessions.length > 0 && (
          <select
            className="chip review-session-select"
            value={current?.session ?? ""}
            onChange={(e) => setSession(e.target.value)}
            aria-label="Session"
          >
            {data.sessions
              .slice()
              .reverse()
              .map((s) => (
                <option key={s} value={s}>
                  {s}
                </option>
              ))}
          </select>
        )}
        {current && <span className="card-asof">fact set v{current.factVersion}</span>}
      </div>

      {isError && <p className="muted">Could not read the review store.</p>}
      {isLoading && !current && <p className="muted">Reading the review store…</p>}
      {!isLoading && !isError && !current && (
        <p className="muted">No fact set has been written yet. The review runs after the close.</p>
      )}

      {current && (
        <>
          <section className={`card review-attention ${flags.length > 0 ? "review-attention-flagged" : ""}`}>
            <div className="card-head">
              <h2>Needs attention</h2>
              {flags.length === 0 && <span className="chip chip-ok">clear</span>}
              {flags.length > 0 && <span className="chip chip-warn">{flags.length}</span>}
            </div>
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
          </section>

          <p className="muted review-note-line">
            No suite total, deliberately: these books differ in scale by more than an order of magnitude, so a
            combined figure would describe the largest one and imply it described all three.
          </p>

          <div className="cards cards-wide">
            {modules.map((m) => (
              <ModuleCard key={m.module} m={m} />
            ))}
          </div>

          {current.note && (
            <section className="card review-note">
              <div className="card-head">
                <h2>Note</h2>
                <span className="chip">interpretation</span>
                <span className="card-asof">written from the fact set above</span>
              </div>
              <NoteMarkdown text={current.note} />
            </section>
          )}
        </>
      )}

      {data && data.allTime.sessions > 0 && (
        <section className="card">
          <div className="card-head">
            <h2>All time</h2>
            <span className="card-asof">
              {data.allTime.sessions} sessions · {data.allTime.from} → {data.allTime.to}
            </span>
          </div>
          <div className="stats-grid">
            {Object.keys(data.allTime.netByModule).map((name) => (
              <div className="stat-tile" key={name}>
                <span className="stat-label">{name}</span>
                <span className={`stat-value ${pnlClass(data.allTime.netByModule[name])}`}>
                  {compactMoney(data.allTime.netByModule[name])}
                </span>
                <span className="stat-label">{count(data.allTime.closedByModule[name])} closed</span>
              </div>
            ))}
          </div>
          <p className="muted review-note-line">
            Summed from the built fact sets, not a fresh pass over the ledgers — so it cannot disagree with the
            sessions above, and its depth is exactly what has been built.
          </p>
        </section>
      )}
    </div>
  );
}
