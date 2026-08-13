import { useState } from "react";
import { useReview } from "../../lib/api";
import type { ReviewArm, ReviewModule } from "@console/shared";
import { NoteMarkdown } from "./NoteMarkdown";

/**
 * The suite review. Renders the fact set and computes nothing.
 *
 * Every figure here comes from `data/review/eod-<day>.json`, the artifact `packages/review` writes.
 * That is the whole point: the markdown render, this page and the narrative read the same file, so
 * they cannot hold different opinions about a session. Where a value is null it renders as "—", never
 * as zero — a cost that was never recorded and a cost of zero are different facts, and conflating
 * them once made this suite's cost model look 90% cheaper than it was.
 */

function money(v: number | null | undefined): string {
  return v === null || v === undefined ? "—" : v.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function pct(v: number | null | undefined): string {
  return v === null || v === undefined ? "—" : `${(v * 100).toFixed(1)}%`;
}

function count(v: number | null | undefined): string {
  return v === null || v === undefined ? "—" : v.toLocaleString();
}

/** What a reader needs before anything else. An empty list is a real and good answer. */
function attention(modules: ReviewModule[]): string[] {
  const out: string[] = [];
  for (const m of modules) {
    if (!m.ok) {
      out.push(`${m.module} could not be read — ${m.reason}`);
      continue;
    }
    if (m.loopTicked === false) out.push(`${m.module} did not tick at all — a stopped loop, not a quiet day`);
    if (m.errors) out.push(`${m.module} logged ${m.errors} iteration error(s)`);
    if (m.suspectedBreak) {
      out.push(
        `${m.module} looks like a regime change with no journaled break — ${m.suspectedBreak.trades} trades ` +
          `against a trailing median of ${Math.round(m.suspectedBreak.trailingMedian)} (${m.suspectedBreak.ratio}x)`,
      );
    }
    if (m.breaks === null) {
      out.push(`${m.module} does not track measurement breaks — its trend assumes a continuity nothing verified`);
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
    .map(([rule, arms]) => `${arms.slice().sort().join(", ")} all centred \`${rule}\``);
}

function ArmRows({ module, arms }: { module: string; arms: ReviewArm[] }) {
  return (
    <>
      {arms.map((a) => (
        <tr key={`${module}-${a.arm}`}>
          <td>{module}</td>
          <td>{a.arm}</td>
          <td className="muted">{a.centredBy ?? "—"}</td>
          <td className="num">{count(a.closed)}</td>
          <td className="num">{money(a.net)}</td>
          <td className="num">{money(a.capitalAtRisk)}</td>
          <td className="num">{pct(a.onMaxRisk)}</td>
          <td className="num">{count(a.wins)}</td>
        </tr>
      ))}
    </>
  );
}

export function ReviewPage() {
  const [session, setSession] = useState<string | undefined>(undefined);
  const { data, isLoading, isError } = useReview(session);

  const current = data?.current ?? null;
  const modules = current?.modules ?? [];
  const armModules = modules.filter((m) => m.ok && m.arms.length > 1);
  const flags = attention(modules);

  return (
    <div className="page">
      <header className="page-head">
        <h1>Suite review</h1>
        {data && data.sessions.length > 0 && (
          <select
            className="select"
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
      </header>

      {isError && <p className="muted">Could not read the review store.</p>}
      {!isLoading && !isError && !current && (
        <p className="muted">No fact set has been written yet. The review runs after the close.</p>
      )}

      {current && (
        <>
          <p className="muted">
            Status <strong>{current.status}</strong> · fact set v{current.factVersion} · paper books
            {current.status === "provisional" && " · the overnight module has not settled; its P&L lands in the next session"}
          </p>

          <section className="card">
            <h2>Needs attention</h2>
            {flags.length === 0 ? (
              <p className="muted">
                Nothing flagged: every module ticked, none errored, and no unjournaled regime change.
              </p>
            ) : (
              <ul className="findings">
                {flags.map((f) => (
                  <li key={f}>{f}</li>
                ))}
              </ul>
            )}
          </section>

          <section className="card">
            <h2>What each module did</h2>
            <p className="muted">
              No suite total, deliberately: these books differ in scale by more than an order of magnitude, so a
              combined figure describes the largest one and implies it describes all three.
            </p>
            <table className="data">
              <thead>
                <tr>
                  <th>Module</th>
                  <th className="num">Closed</th>
                  <th className="num">Net</th>
                  <th className="num">Capital at risk</th>
                  <th className="num">Return on risk</th>
                  <th className="num">Wins</th>
                  <th className="num">Raw n</th>
                  <th className="num">Events</th>
                </tr>
              </thead>
              <tbody>
                {modules.map((m) => (
                  <tr key={m.module}>
                    <td>{m.module}</td>
                    <td className="num">{m.ok ? count(m.closed) : "—"}</td>
                    <td className="num">{m.ok ? money(m.net) : "—"}</td>
                    <td className="num">{money(m.capitalAtRisk)}</td>
                    <td className="num">{pct(m.onMaxRisk)}</td>
                    <td className="num">{m.ok ? count(m.wins) : "—"}</td>
                    <td className="num">{count(m.n)}</td>
                    <td className="num">{count(m.effectiveN)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <p className="muted">
              <strong>Events</strong> is the independent-observation count: trades sharing a symbol and session share
              one market event. A book with hundreds of trades on one name in one session has one event, not hundreds.
            </p>
          </section>

          {armModules.length > 0 && (
            <section className="card">
              <h2>By arm</h2>
              <p className="muted">
                Same underlying, same sessions — a paired comparison, which is why these are worth more than their
                sample size alone suggests.
              </p>
              <table className="data">
                <thead>
                  <tr>
                    <th>Module</th>
                    <th>Arm</th>
                    <th>Centred by</th>
                    <th className="num">Closed</th>
                    <th className="num">Net</th>
                    <th className="num">Capital at risk</th>
                    <th className="num">Return on risk</th>
                    <th className="num">Wins</th>
                  </tr>
                </thead>
                <tbody>
                  {armModules.map((m) => (
                    <ArmRows key={m.module} module={m.module} arms={m.arms} />
                  ))}
                </tbody>
              </table>
              {armModules.flatMap((m) => collapsedArms(m).map((c) => `${m.module}: ${c}`)).length > 0 && (
                <>
                  <p className="muted">
                    Arms sharing a centring rule for a whole session are <strong>not independent that session</strong> —
                    a GEX-centred arm degrades to ATM when the streamer has no open interest cached, at which point it
                    is the control arm under another name. Read their agreement as one arm run twice.
                  </p>
                  <ul className="findings">
                    {armModules.flatMap((m) =>
                      collapsedArms(m).map((c) => <li key={`${m.module}-${c}`}>{`${m.module}: ${c}`}</li>),
                    )}
                  </ul>
                </>
              )}
            </section>
          )}

          <section className="card">
            <h2>Expected against observed</h2>
            <p className="muted">Each module against its own model — they are not comparable with each other.</p>
            <ul className="findings">
              {modules
                .filter((m) => m.ok)
                .map((m) => (
                  <li key={m.module}>
                    <strong>{m.module}</strong> ({m.expectedBasis ?? "n/a"}):{" "}
                    {m.expected === null && m.observed === null
                      ? "nothing to compare this session"
                      : `expected ${money(m.expected)}, observed ${money(m.observed)}`}
                    {m.expected === null && m.observed !== null && " (no model recorded for this session)"}
                  </li>
                ))}
            </ul>
          </section>

          {current.note && (
            <section className="card">
              <h2>Note</h2>
              <p className="muted">
                Interpretation, not measurement — written from the fact set above, which is the only input. Where the
                two disagree, the artifact is right.
              </p>
              <NoteMarkdown text={current.note} />
            </section>
          )}
        </>
      )}

      {data && data.allTime.sessions > 0 && (
        <section className="card">
          <h2>All time</h2>
          <p className="muted">
            Summed from {data.allTime.sessions} built fact sets, {data.allTime.from} to {data.allTime.to} — not a fresh
            pass over the ledgers, so it cannot disagree with the sessions above, and its depth is exactly what has been
            built.
          </p>
          <table className="data">
            <thead>
              <tr>
                <th>Module</th>
                <th className="num">Closed</th>
                <th className="num">Net</th>
              </tr>
            </thead>
            <tbody>
              {Object.keys(data.allTime.netByModule).map((name) => (
                <tr key={name}>
                  <td>{name}</td>
                  <td className="num">{count(data.allTime.closedByModule[name])}</td>
                  <td className="num">{money(data.allTime.netByModule[name])}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      )}
    </div>
  );
}
