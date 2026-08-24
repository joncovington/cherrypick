import { useState, type ReactNode } from "react";
import { useMorningReport } from "../../lib/api";
import type { MorningGate, MorningPack, MorningReading, MorningSectorRow } from "@console/shared";
import { NoteMarkdown } from "../Review/NoteMarkdown";
import { LevelStrip } from "../../components/Charts";

/**
 * The morning report. Renders the fact pack and computes nothing.
 *
 * Every figure comes from `data/overview/morning-<day>.json`, the artifact `packages/overview`
 * writes — the markdown render, this page and the narrative all read that one file, so they cannot
 * hold different opinions about a session. Phase, gate verdicts and strongest/weakest sectors are
 * the pack's precomputed answers, displayed as-is.
 *
 * Built from the console's own primitives (`card`, `stats-grid`/`stat-tile`, `data-table`, `chip`,
 * `dot`) rather than a parallel look — the Review page records why inventing class names goes wrong.
 *
 * Two rules the styling has to carry, not just the numbers:
 * - **Null is not zero.** An unmeasured reading renders as an em dash, never 0.00.
 * - **Provenance stays visible.** A prior-close stand-in is a different fact from a live pre-open
 *   reading, so it renders muted with its own session date beside it.
 */

function fmt(v: number | null | undefined, digits = 2): string {
  if (v === null || v === undefined) return "—";
  return v.toLocaleString(undefined, { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

function signedPct(v: number | null | undefined): string {
  if (v === null || v === undefined) return "—";
  return `${v > 0 ? "+" : ""}${v.toFixed(2)}%`;
}

function pnlClass(v: number | null | undefined): string {
  if (v === null || v === undefined || v === 0) return "";
  return v > 0 ? "pnl-pos" : "pnl-neg";
}

/** The scorecard order — the pack may carry more readings; these are the six the page fronts. */
const SCORECARD = ["spx", "vix", "vix3m", "vvix", "wti_proxy", "gold_proxy"];

function basisLabel(r: MorningReading): string {
  if (r.basis === "live") return "live pre-open";
  if (r.basis === "prior") return `prior (${r.session ?? "unknown session"})`;
  return "unmeasured";
}

function ReadingTile({ id, r }: { id: string; r: MorningReading }) {
  const stale = r.basis !== "live";
  return (
    <div className="stat-tile">
      <span className="stat-label">{r.label ?? id}</span>
      <span className={`stat-value ${stale ? "muted" : ""}`}>{fmt(r.value)}</span>
      <span className={`stat-label ${stale ? "muted" : ""}`}>{basisLabel(r)}</span>
    </div>
  );
}

function gateChip(status: string): { cls: string; text: string } {
  if (status === "met") return { cls: "chip-ok", text: "met" };
  if (status === "not_met") return { cls: "chip-warn", text: "not met" };
  return { cls: "chip-missing", text: "unknown" };
}

function GateRow({ g }: { g: MorningGate }) {
  const chip = gateChip(g.status);
  return (
    <tr>
      <td>
        <span className={`chip ${chip.cls}`}>{chip.text}</span>
      </td>
      <td>{g.label}</td>
      <td className="muted">{g.detail ?? "—"}</td>
    </tr>
  );
}

/** Ranked by move, nulls last — an unmeasured sector sinks, it does not read as flat. */
function rankedBoard(board: MorningSectorRow[]): MorningSectorRow[] {
  return [...board].sort((a, b) => {
    if (a.changePct === null && b.changePct === null) return 0;
    if (a.changePct === null) return 1;
    if (b.changePct === null) return -1;
    return b.changePct - a.changePct;
  });
}

function phaseChip(phase: string): string {
  if (phase === "green") return "chip-ok";
  if (phase === "yellow") return "chip-warn";
  return "chip-missing"; // the console's err-toned chip — red is an error state, not a variant
}

function PhaseBanner({ pack }: { pack: MorningPack }) {
  const phase = pack.phase;
  return (
    <section className="card">
      <div className="card-head">
        <h2>Session phase</h2>
        {phase ? (
          <span className={`chip ${phaseChip(phase.phase)}`}>{phase.phase.toUpperCase()}</span>
        ) : (
          <span className="chip chip-missing">unknown</span>
        )}
        {phase && phase.gatesMet !== null && phase.gatesMeasured !== null && (
          <span className="card-asof">
            {phase.gatesMet} met of {phase.gatesMeasured} measured
          </span>
        )}
      </div>
      <p className={phase ? "" : "muted"}>{phase?.reason ?? "The pack carries no phase verdict."}</p>
    </section>
  );
}

const ZONE_LABEL: Record<string, string> = {
  full: "FULL DEPLOY",
  reduced: "REDUCED",
  defensive: "DEFENSIVE",
};

/**
 * The deployment score.
 *
 * The whole styling problem of this card is that it must not read as an instruction. The number is
 * record-only — it gates nothing and sizes nothing — and a big confident figure in a trading UI
 * reads as a directive whether or not the caption says otherwise. So the zone renders as a neutral
 * chip rather than the ok/warn colours the phase banner uses, the pack's own "governs nothing"
 * sentence is rendered verbatim beside it, and the card sits BELOW the session phase, which is the
 * verdict that actually decides anything. The two are free to disagree; that disagreement is the
 * point of recording this at all.
 */
function DeploymentCard({ pack }: { pack: MorningPack }) {
  // Absent, not just null. The type says `MorningDeployment | null`, but the payload crosses a wire
  // from a server process that may be older than this bundle — a long-running console outlives its
  // own rebuilds — and a server that predates the field omits it entirely. `=== null` sails past
  // undefined and the whole page dies on the first property read, which is the exact failure the
  // reader's own "tolerate an unfamiliar shape" rule exists to prevent.
  const d = pack.deployment;
  if (!d) return null;
  const measured = (s: { status: string }) => s.status === "measured";
  return (
    <section className="card">
      <div className="card-head">
        <h2>Deployment score</h2>
        <span className="chip">record-only</span>
        {d.zone !== null && <span className="chip">{ZONE_LABEL[d.zone] ?? d.zone}</span>}
        {d.signalsMeasured !== null && d.signalsTotal !== null && (
          <span className="card-asof">
            {d.signalsMeasured} of {d.signalsTotal} signals measured
          </span>
        )}
      </div>

      {d.score === null ? (
        <p className="muted">No score — {d.reason ?? "too few signals measured to blend one."}</p>
      ) : (
        <div className="stats-grid">
          <div className="stat-tile">
            <span className="stat-label">score</span>
            <span className="stat-value">{fmt(d.score, 1)}</span>
            <span className="stat-label muted">of 100</span>
          </div>
          {d.signals.map((s) => (
            <div key={s.id} className="stat-tile">
              <span className="stat-label">{s.label ?? s.id}</span>
              <span className={`stat-value ${measured(s) ? "" : "muted"}`}>
                {measured(s) ? fmt(s.score, 0) : "—"}
              </span>
              <span className="stat-label muted">
                {s.weight === null ? "" : `weight ${(s.weight * 100).toFixed(0)}%`}
              </span>
            </div>
          ))}
        </div>
      )}

      {d.signals.length > 0 && (
        <table className="data-table">
          <thead>
            <tr>
              <th>Signal</th>
              <th>Score</th>
              <th>Detail</th>
            </tr>
          </thead>
          <tbody>
            {d.signals.map((s) => (
              <tr key={s.id}>
                <td>{s.label ?? s.id}</td>
                <td className={measured(s) ? "" : "muted"}>{measured(s) ? fmt(s.score, 1) : "—"}</td>
                <td className="muted">{s.detail ?? "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      <p className="muted">
        {d.note ?? "A recorded measurement — it feeds no gate, no phase and no sizing."}
        {d.weightsRenormalized === true && " Weights were renormalized over the measured signals."}
        {d.deferred.length > 0 && ` Deferred: ${d.deferred.join(", ")}.`}
      </p>
    </section>
  );
}

function CalendarCard({ pack }: { pack: MorningPack }) {
  const c = pack.calendar;
  const yesNo = (v: boolean | null) => (v === null ? "—" : v ? "yes" : "no");
  return (
    <section className="card">
      <div className="card-head">
        <h2>Calendar</h2>
      </div>
      {c === null ? (
        <p className="muted">No calendar facts in this pack.</p>
      ) : (
        <div className="stats-grid">
          <div className="stat-tile">
            <span className="stat-label">FOMC today</span>
            <span className={`stat-value ${c.isFomcDay ? "" : "muted"}`}>{yesNo(c.isFomcDay)}</span>
          </div>
          <div className="stat-tile">
            <span className="stat-label">Next FOMC</span>
            <span className={`stat-value ${c.nextFomc === null ? "muted" : ""}`}>{c.nextFomc ?? "—"}</span>
            {c.fomcYearKnown === false && <span className="stat-label muted">year not yet declared</span>}
          </div>
          <div className="stat-tile">
            <span className="stat-label">Triple witching</span>
            <span className={`stat-value ${c.isTripleWitching ? "" : "muted"}`}>{yesNo(c.isTripleWitching)}</span>
          </div>
          <div className="stat-tile">
            <span className="stat-label">Quarterly expiry</span>
            <span className={`stat-value ${c.isQuarterlyExpiry ? "" : "muted"}`}>{yesNo(c.isQuarterlyExpiry)}</span>
          </div>
          <div className="stat-tile">
            <span className="stat-label">Next trading day</span>
            <span className={`stat-value ${c.nextTradingDay === null ? "muted" : ""}`}>{c.nextTradingDay ?? "—"}</span>
          </div>
        </div>
      )}
    </section>
  );
}

/** `tabs` is the Reports page's tab strip, rendered inside this page's own title row. Optional so
 *  the page still stands alone if it is ever routed to directly. */
export function MorningPage({ tabs }: { tabs?: ReactNode } = {}) {
  const [session, setSession] = useState<string | undefined>(undefined);
  const { data, isLoading, isError } = useMorningReport(session);

  const current = data?.current ?? null;
  const levels = current?.levels ?? null;
  const sectors = current?.sectors ?? null;

  return (
    <div className="page">
      <div className="page-title-row">
        <h1>Morning report</h1>
        {tabs}
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
        {current && <span className="card-asof">fact pack v{current.factVersion ?? "?"}</span>}
      </div>

      {isError && <p className="muted">Could not read the overview store.</p>}
      {isLoading && !current && <p className="muted">Reading the overview store…</p>}
      {!isLoading && !isError && !current && (
        <p className="muted">No morning pack has been written yet. The report runs before the open.</p>
      )}

      {current && (
        <>
          <PhaseBanner pack={current} />

          <section className="card">
            <div className="card-head">
              <h2>Scorecard</h2>
              {current.generatedAt !== null && <span className="card-asof">generated {current.generatedAt}</span>}
            </div>
            <div className="stats-grid">
              {SCORECARD.map((id) => {
                const r = current.readings[id];
                return r !== undefined ? <ReadingTile key={id} id={id} r={r} /> : null;
              })}
            </div>
          </section>

          <div className="cards cards-wide">
            <section className="card">
              <div className="card-head">
                <h2>Gamma levels {levels?.symbol !== null && levels?.symbol !== undefined ? `· ${levels.symbol}` : ""}</h2>
                {levels?.session !== null && levels?.session !== undefined && (
                  <span className="card-asof">as of {levels.session}</span>
                )}
              </div>
              {levels === null ? (
                <p className="muted">No levels in this pack.</p>
              ) : (
                <>
                  {/* The levels' spatial relationship is the reading — "just above the flip, short
                      of the call wall" — and a row of tiles makes the reader do that arithmetic.
                      The tiles stay beneath for the exact figures and their basis labels. */}
                  <LevelStrip
                    levels={[
                      { label: "put wall", value: levels.putWall, color: "#d95c4a" },
                      { label: "flip", value: levels.zeroGamma, color: "#7aa2ff" },
                      { label: "call wall", value: levels.callWall, color: "#43b57a" },
                    ]}
                    marker={{
                      label: "spot",
                      value: levels.referencePrice,
                      muted: levels.referenceBasis !== "live",
                    }}
                  />
                  <div className="stats-grid">
                  <div className="stat-tile">
                    <span className="stat-label">zero gamma (flip)</span>
                    <span className="stat-value">{fmt(levels.zeroGamma, 0)}</span>
                  </div>
                  <div className="stat-tile">
                    <span className="stat-label">call wall</span>
                    <span className="stat-value">{fmt(levels.callWall, 0)}</span>
                  </div>
                  <div className="stat-tile">
                    <span className="stat-label">put wall</span>
                    <span className="stat-value">{fmt(levels.putWall, 0)}</span>
                  </div>
                  <div className="stat-tile">
                    <span className="stat-label">reference price</span>
                    <span className={`stat-value ${levels.referenceBasis !== "live" ? "muted" : ""}`}>
                      {fmt(levels.referencePrice)}
                    </span>
                    {levels.referenceBasis !== null && (
                      <span className="stat-label muted">{levels.referenceBasis}</span>
                    )}
                  </div>
                  </div>
                </>
              )}
            </section>

            <section className="card">
              <div className="card-head">
                <h2>Gates</h2>
                {current.phase !== null && current.phase.gatesTotal !== null && (
                  <span className="card-asof">{current.phase.gatesTotal} declared</span>
                )}
              </div>
              {current.gates.length === 0 ? (
                <p className="muted">No gates in this pack.</p>
              ) : (
                <table className="data-table">
                  <thead>
                    <tr>
                      <th>Status</th>
                      <th>Gate</th>
                      <th>Detail</th>
                    </tr>
                  </thead>
                  <tbody>
                    {current.gates.map((g) => (
                      <GateRow key={g.id} g={g} />
                    ))}
                  </tbody>
                </table>
              )}
            </section>
          </div>

          <section className="card">
            <div className="card-head">
              <h2>Sector board</h2>
              {sectors !== null && sectors.measured !== null && (
                <span className="card-asof">{sectors.measured} of {sectors.board.length} measured</span>
              )}
              {sectors?.strongest && (
                <span className="chip chip-ok">strongest {sectors.strongest.symbol}</span>
              )}
              {sectors?.weakest && (
                <span className="chip chip-warn">weakest {sectors.weakest.symbol}</span>
              )}
            </div>
            {sectors === null || sectors.board.length === 0 ? (
              <p className="muted">No sector board in this pack.</p>
            ) : (
              <table className="data-table">
                <thead>
                  <tr>
                    <th>Symbol</th>
                    <th>Sector</th>
                    <th>Change</th>
                    <th>Close</th>
                  </tr>
                </thead>
                <tbody>
                  {rankedBoard(sectors.board).map((row) => (
                    <tr key={row.symbol}>
                      <td>{row.symbol}</td>
                      <td className="muted">{row.sector ?? "—"}</td>
                      <td className={pnlClass(row.changePct)}>{signedPct(row.changePct)}</td>
                      <td className={row.close === null ? "muted" : ""}>{fmt(row.close)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </section>

          <DeploymentCard pack={current} />

          <CalendarCard pack={current} />

          {data?.note ? (
            <section className="card review-note">
              <div className="card-head">
                <h2>Narrative</h2>
                <span className="chip">interpretation</span>
                <span className="card-asof">written from the fact pack above</span>
              </div>
              <NoteMarkdown text={data.note} />
            </section>
          ) : (
            <p className="muted">No narrative for this session.</p>
          )}
        </>
      )}
    </div>
  );
}
