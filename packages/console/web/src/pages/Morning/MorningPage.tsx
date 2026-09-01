import { useState, type ReactNode } from "react";
import { useMorningReport } from "../../lib/api";
import type {
  MorningGate,
  MorningPack,
  MorningReading,
  MorningSectorRow,
  MorningVolCurvePoint,
  MorningVolPercentile,
} from "@console/shared";
import { NoteMarkdown } from "../Review/NoteMarkdown";
import { AXIS_FONT, SERIES_COLORS, LevelStrip } from "../../components/Charts";

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

/**
 * The vol term structure.
 *
 * Two rules shape this card, and both are about what it must NOT say.
 *
 * It is record-only and feeds no gate, so — like the deployment score below it — the shape renders
 * as a neutral chip rather than borrowing the phase banner's ok/warn colours. "Backwardation" is a
 * fact about the curve, not an instruction about the session.
 *
 * And a refused percentile renders as a refusal WITH its sample count, never as a blank or a zero.
 * That is the common case rather than the edge: every reading but VIX and VIX3M had no stored
 * history at all until the producer's Summary subscription was repaired, and a percentile bar that
 * silently drew nothing would be indistinguishable from one sitting at the bottom of its range.
 */
// Not `var(--accent)`: that is the brand red, and red on a trading surface reads as loss or
// alert. This card states a neutral fact and gates nothing, so it borrows the chart palette's
// amber -- the same colour the reference panel this was modelled on used.
const MONTHS = ["January", "February", "March", "April", "May", "June", "July",
  "August", "September", "October", "November", "December"];

const VOL_INK = SERIES_COLORS[2];

function VolCurve({ points }: { points: MorningVolCurvePoint[] }) {
  const measured = points.filter((p) => p.value !== null && p.dte !== null);
  if (measured.length < 2) return <p className="muted">not enough of the curve is measured to draw it</p>;
  const width = 620;
  const height = 150;
  const m = { l: 34, r: 14, t: 14, b: 22 };
  const ys = measured.map((p) => p.value as number);
  const lo = Math.min(...ys);
  const hi = Math.max(...ys);
  const pad = (hi - lo || 1) * 0.18;
  // Evenly spaced by POSITION, not by days: a linear time axis crushes 9D/30D/3M into the left edge
  // and makes the front of the curve — where event pricing actually shows up — unreadable.
  const X = (i: number) => m.l + (i / (measured.length - 1)) * (width - m.l - m.r);
  const Y = (v: number) =>
    m.t + ((hi + pad - v) / (hi - lo + 2 * pad || 1)) * (height - m.t - m.b);
  const path = measured.map((p, i) => `${i ? "L" : "M"}${X(i)},${Y(p.value as number)}`).join(" ");
  return (
    <svg viewBox={`0 0 ${width} ${height}`} className="chart" role="img" aria-label="VIX term structure">
      <path d={path} fill="none" stroke={VOL_INK} strokeWidth={2} />
      {measured.map((p, i) => (
        <g key={p.symbol}>
          <circle cx={X(i)} cy={Y(p.value as number)} r={3.5} fill={VOL_INK} />
          <text x={X(i)} y={Y(p.value as number) - 8} textAnchor="middle" {...AXIS_FONT}>
            {fmt(p.value, 2)}
          </text>
          <text x={X(i)} y={height - 6} textAnchor="middle" {...AXIS_FONT}>
            {p.symbol}
          </text>
        </g>
      ))}
    </svg>
  );
}

function PercentileRow({ id, p }: { id: string; p: MorningVolPercentile }) {
  // Three refusals, three sentences. "no daily series" is PERMANENT and must not read like the
  // temporary one beside it -- the pack declares it for a reading whose live quote works but whose
  // history the feed does not serve, and a row promising a gap that never closes is a row a reader
  // learns to skip.
  const refusal =
    p.reason === "reading_unmeasured"
      ? "not served by the feed"
      : p.reason === "no_daily_series"
        ? "no daily series available"
        : p.reason === "too_few_closes"
          ? `only ${p.samples ?? 0} closes on file`
          : (p.reason ?? "unavailable");
  return (
    <div className="pct-row">
      <span className="stat-label">{id.toUpperCase()}</span>
      <span className="stat-value">{fmt(p.value, 2)}</span>
      {p.percentile === null ? (
        <span className="muted pct-refusal">{refusal}</span>
      ) : (
        <>
          <span className="pct-bar" aria-hidden>
            <span className="pct-fill" style={{ width: `${Math.max(1, Math.min(100, p.percentile))}%` }} />
          </span>
          <span className="stat-label muted">
            {fmt(p.percentile, 0)}th pctile of {p.samples ?? 0}
          </span>
        </>
      )}
    </div>
  );
}

function VolRegimeCard({ pack }: { pack: MorningPack }) {
  // Absent, not just null — same reason as DeploymentCard: a server process older than this bundle
  // omits the field entirely, and `=== null` sails straight past undefined.
  const v = pack.volRegime;
  if (!v) return null;
  const season = v.seasonality;
  return (
    <section className="card">
      <div className="card-head">
        <h2>Vol term structure</h2>
        <span className="chip">record-only</span>
        {v.shape !== null && <span className="chip">{v.shape}</span>}
        {v.measuredPoints !== null && v.totalPoints !== null && (
          <span className="card-asof">
            {v.measuredPoints} of {v.totalPoints} points measured
          </span>
        )}
      </div>

      <VolCurve points={v.curve} />

      <div className="stats-grid">
        <div className="stat-tile">
          <span className="stat-label">front 9D→30D</span>
          <span className="stat-value">{signedPct(v.slope.front9d30dPct)}</span>
          <span className="stat-label muted">event pricing</span>
        </div>
        <div className="stat-tile">
          <span className="stat-label">mid 30D→3M</span>
          <span className="stat-value">{signedPct(v.slope.mid30d3mPct)}</span>
          <span className="stat-label muted">
            {v.vixVix3mRatio === null ? "ratio —" : `VIX/VIX3M ${fmt(v.vixVix3mRatio, 3)}`}
          </span>
        </div>
        <div className="stat-tile">
          <span className="stat-label">back 9D→1Y</span>
          <span className="stat-value">{signedPct(v.slope.back9d1yPct)}</span>
          <span className="stat-label muted">structural carry</span>
        </div>
      </div>

      {v.shape === null && (
        <p className="muted">No regime label — {v.shapeReason ?? "the ratio could not be measured."}</p>
      )}

      <div className="pct-list">
        {Object.entries(v.percentiles).map(([id, p]) => (
          <PercentileRow key={id} id={id} p={p} />
        ))}
      </div>

      {season && (
        <p className="muted">
          {season.norm === null
            ? `No seasonal norm — ${season.reason === "too_few_years" ? `only ${season.years ?? 0} years on file` : (season.reason ?? "unavailable")}.`
            : `VIX is ${signedPct(season.vixVsNormPct)} against its ${MONTHS[(season.month ?? 1) - 1] ?? "monthly"} norm of ${fmt(season.norm, 2)}, across ${season.years ?? 0} years.`}
        </p>
      )}
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

          <VolRegimeCard pack={current} />
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
