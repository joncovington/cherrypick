import { useState, type ReactNode } from "react";
import { useMorningReport } from "../../lib/api";
import type {
  MorningGate,
  MorningPack,
  MorningReading,
  MorningSectorRow,
  MorningVolCurvePoint,
  MorningVolPercentile,
  MorningFuture,
  MorningHotOptions,
  MorningHotOptionsRow,
} from "@console/shared";
import { NoteMarkdown } from "../Review/NoteMarkdown";
import { AXIS_FONT, SERIES_COLORS, LevelStrip } from "../../components/Charts";
import { TechnicalsCards } from "./TechnicalsCards";
import { useFeatures } from "../../lib/useFeatures";
import { isFeatureOn } from "../../lib/visibility";
import { Card, SortTh, sortRows, useSort } from "../../components/DataTable";
import { HelpSection, type CardHelpContent } from "../../components/CardHelp";
import { FilterGroup } from "../../components/FilterSort";
import { Section } from "../../components/Section";
import { useFilterPanel } from "../useFilterPanel";

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

/**
 * The scorecard order — the pack may carry more readings; these are the ones the page fronts.
 *
 * The S&P, crude and gold tiles are the /ES, /CL and /GC futures from the pre-market tape, which
 * trade before the open when SPX and the ETFs do not. Each is paired with the reading a pack
 * carried before it had that future (no tape before fact version 4, no /GC before 2026-10-01).
 * That reading only stands in when the pack has no such future at all: a future the pack holds
 * but could not measure renders unmeasured, because swapping in SPX's close or USO would quietly
 * answer a different question. Each reading's own label says what it is.
 */
const SCORECARD: { reading: string; future?: string }[] = [
  { reading: "spx", future: "es" },
  { reading: "vix" },
  { reading: "vix3m" },
  { reading: "vvix" },
  { reading: "wti_proxy", future: "cl" },
  { reading: "gold_proxy", future: "gc" },
];

function scorecardTile(pack: MorningPack, reading: string, future?: string): ReactNode {
  const f = future !== undefined ? pack.premarket?.futures[future] : undefined;
  if (f !== undefined) return <ReadingTile key={reading} id={future!} r={f} />;
  const r = pack.readings[reading];
  return r !== undefined ? <ReadingTile key={reading} id={reading} r={r} /> : null;
}

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
    <Card
      title="Session phase"
      className="lv-12"
      controls={
        <>
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
        </>
      }
    >
      <p className={phase ? "" : "muted"}>{phase?.reason ?? "The pack carries no phase verdict."}</p>
    </Card>
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
function deploymentHelp(d: NonNullable<MorningPack["deployment"]>): CardHelpContent {
  return {
    title: "The deployment score",
    body: (
      <HelpSection title="Record-only">
        <p>
          {d.note ?? "A recorded measurement — it feeds no gate, no phase and no sizing."}
          {d.weightsRenormalized === true && " Weights were renormalized over the measured signals."}
          {d.deferred.length > 0 && ` Deferred: ${d.deferred.join(", ")}.`}
        </p>
      </HelpSection>
    ),
  };
}

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
    <Card
      className="lv-12"
      title="Deployment score" flag="record-only" help={deploymentHelp(d)}
      controls={
        <>
          {d.zone !== null && <span className="chip">{ZONE_LABEL[d.zone] ?? d.zone}</span>}
          {d.signalsMeasured !== null && d.signalsTotal !== null && (
            <span className="card-asof">
              {d.signalsMeasured} of {d.signalsTotal} signals measured
            </span>
          )}
        </>
      }
    >

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
        <div className="table-scroll">
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
        </div>
      )}

    </Card>
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
      <span className="stat-label" title={p.source ?? undefined}>
        {id.toUpperCase()}
      </span>
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
            {/* The Cboe file's prior close is a different fact from a live read; say so. */}
            {p.source === "cboe_file_prior_close" && " · prior close"}
          </span>
        </>
      )}
    </div>
  );
}

const VOL_HELP: CardHelpContent = {
  title: "The vol term structure",
  body: (
    <HelpSection title="Record-only">
      <p>
        The shape is a fact about the curve, not an instruction about the session, and feeds no gate. A percentile the history cannot support
        is shown as a refusal with its sample count, never as a blank or a zero.
      </p>
    </HelpSection>
  ),
};

function VolRegimeCard({ pack }: { pack: MorningPack }) {
  // Absent, not just null — same reason as DeploymentCard: a server process older than this bundle
  // omits the field entirely, and `=== null` sails straight past undefined.
  const v = pack.volRegime;
  if (!v) return null;
  const season = v.seasonality;
  return (
    <Card
      title="Vol term structure" flag="record-only" help={VOL_HELP}
      controls={
        <>
          {v.shape !== null && <span className="chip">{v.shape}</span>}
          {v.measuredPoints !== null && v.totalPoints !== null && (
            <span className="card-asof">
              {v.measuredPoints} of {v.totalPoints} points measured
            </span>
          )}
        </>
      }
    >

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

      {v.riskReversal && (
        <div className="stats-grid">
          <div className="stat-tile">
            <span className="stat-label">25Δ risk reversal, {v.riskReversal.targetDte ?? 30}D</span>
            <span className="stat-value">
              {v.riskReversal.rrVolPts === null
                ? "—"
                : `${v.riskReversal.rrVolPts > 0 ? "+" : ""}${fmt(v.riskReversal.rrVolPts, 2)} pts`}
            </span>
            <span className="stat-label muted">call IV − put IV · SPX, close of {v.riskReversal.session ?? "—"}</span>
          </div>
          <div className="stat-tile">
            <span className="stat-label">25Δ call IV</span>
            <span className="stat-value">{fmt(v.riskReversal.call25dIvPct, 2)}%</span>
          </div>
          <div className="stat-tile">
            <span className="stat-label">25Δ put IV</span>
            <span className="stat-value">{fmt(v.riskReversal.put25dIvPct, 2)}%</span>
            <span className="stat-label muted">
              {v.riskReversal.expirations.map((e) => `${e.expiration} (${e.dte ?? "?"}D)`).join(" / ")}
            </span>
          </div>
        </div>
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
    </Card>
  );
}

function calendarHelp(c: MorningPack["calendar"]): CardHelpContent {
  const asOf = c?.earnings?.asOf ?? null;
  return {
    title: "The calendar",
    body: (
      <HelpSection title="Implied move">
        <p>
          Implied move = 0.85 × the at-the-money straddle on the first expiration after the print, priced at the close
          {asOf !== null ? ` (${asOf.slice(0, 16).replace("T", " ")} UTC)` : ""}.
        </p>
      </HelpSection>
    ),
  };
}

function CalendarCard({ pack }: { pack: MorningPack }) {
  const c = pack.calendar;
  const yesNo = (v: boolean | null) => (v === null ? "—" : v ? "yes" : "no");
  // Implied move sorts by its percentage, the figure that compares across symbols.
  const earnSort = useSort();
  const earnings = sortRows(c?.earnings?.rows ?? [], earnSort, {
    date: (r) => r.date,
    symbol: (r) => r.symbol,
    move: (r) => r.expectedMovePct,
    expiration: (r) => r.expiration,
    spot: (r) => r.spot,
  });
  return (
    <Card title="Calendar" help={calendarHelp(c)}>
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

      {c?.releases && (
        <>
          <h3>Scheduled releases, next seven days</h3>
          {c.releases.length === 0 ? (
            <p className="muted">None on file.</p>
          ) : (
            <div className="table-scroll">
            <table className="data-table">
              <thead>
                <tr>
                  <th>Date</th>
                  <th>Time (ET)</th>
                  <th>Release</th>
                  <th>Source</th>
                </tr>
              </thead>
              <tbody>
                {c.releases.map((r) => (
                  <tr key={`${r.date}-${r.name}`}>
                    <td>
                      {r.date}
                      {r.today && <span className="chip"> today</span>}
                    </td>
                    {/* FRED publishes dates without times; an em dash, never a guessed 08:30. */}
                    <td className={r.timeEt === null ? "muted" : ""}>{r.timeEt ?? "—"}</td>
                    <td>{r.name}</td>
                    <td className="muted">{r.source ?? "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            </div>
          )}
        </>
      )}

      {c?.earnings && (
        <>
          <h3>Earnings, next seven days</h3>
          {c.earnings.reason !== null ? (
            <p className="muted">Not available ({c.earnings.reason}).</p>
          ) : c.earnings.rows.length === 0 ? (
            <p className="muted">No announcements among the covered stocks.</p>
          ) : (
            <div className="table-scroll">
            <table className="data-table">
              <thead>
                <tr>
                  <SortTh label="Date" k="date" sort={earnSort} />
                  <th>When</th>
                  <SortTh label="Symbol" k="symbol" sort={earnSort} />
                  <SortTh label="Implied move" k="move" sort={earnSort} title="sorts by the move as a percentage of spot" />
                  <SortTh label="Expiration" k="expiration" sort={earnSort} />
                  <SortTh label="Spot" k="spot" sort={earnSort} />
                </tr>
              </thead>
              <tbody>
                {earnings.map((r) => (
                  <tr key={`${r.date}-${r.symbol}`}>
                    <td>{r.date}</td>
                    <td className="muted">
                      {r.when === "Before market open" ? "before open" : r.when === "After market close" ? "after close" : "—"}
                    </td>
                    <td>{r.symbol}</td>
                    {r.expectedMove === null ? (
                      <td className="muted">not priced ({r.reason ?? "no reason recorded"})</td>
                    ) : (
                      <td>
                        ±${fmt(r.expectedMove)} (±{fmt(r.expectedMovePct, 1)}%)
                      </td>
                    )}
                    <td className="muted">{r.expiration ?? "—"}</td>
                    <td>{fmt(r.spot)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            </div>
          )}
        </>
      )}
    </Card>
  );
}

const FUTURES_ORDER = ["es", "nq", "ym", "rty", "cl", "bz", "gc"];

/** Why a future shows no change — the pack's reason codes, in words. */
function changeReason(f: MorningFuture): string {
  if (f.changeReason === "no_live_print") return "no live print yet";
  if (f.changeReason === "no_prior_settle") return "no settle on file";
  return f.changeReason ?? "—";
}

/**
 * Futures and the indexes the scorecard does not front. The change is against the prior SETTLE and
 * is only shown for a live print: over a weekend the "last" is Friday's own close, and a change of
 * a price against itself read 0.00% — a measured-looking figure that measured nothing.
 */
function PremarketCard({ pack }: { pack: MorningPack }) {
  const p = pack.premarket;
  if (!p) return null;
  const futures = [
    ...FUTURES_ORDER.filter((k) => p.futures[k] !== undefined),
    ...Object.keys(p.futures).filter((k) => !FUTURES_ORDER.includes(k)),
  ];
  return (
    <Card
      title="Pre-market"
      controls={
        <>
          {p.measuredFutures !== null && (
            <span className="card-asof">
              {p.measuredFutures} of {futures.length} futures live
            </span>
          )}
        </>
      }
    >
      <div className="table-scroll">
      <table className="data-table">
        <thead>
          <tr>
            <th>Contract</th>
            <th>Last</th>
            <th>vs settle</th>
            <th>Prior settle</th>
          </tr>
        </thead>
        <tbody>
          {futures.map((k) => {
            const f = p.futures[k]!;
            return (
              <tr key={k}>
                <td>{f.label ?? k}</td>
                <td className={f.basis === "live" ? "" : "muted"}>
                  {fmt(f.value)}
                  {f.basis !== "live" && <span className="muted"> ({f.session ?? "prior"})</span>}
                </td>
                {f.changeVsPriorClosePct === null ? (
                  <td className="muted">{changeReason(f)}</td>
                ) : (
                  <td className={pnlClass(f.changeVsPriorClosePct)}>{signedPct(f.changeVsPriorClosePct)}</td>
                )}
                <td className="muted">
                  {fmt(f.priorSettle)}
                  {f.priorSettleSession !== null && ` (${f.priorSettleSession})`}
                </td>
              </tr>
            );
          })}
          {Object.entries(p.indexes).map(([k, r]) => (
            <tr key={k}>
              <td>{r.label ?? k}</td>
              <td className={r.basis === "live" ? "" : "muted"}>
                {fmt(r.value)}
                {r.basis !== "live" && <span className="muted"> ({r.session ?? "prior"})</span>}
              </td>
              <td className={pnlClass(r.priorChangePct)}>{signedPct(r.priorChangePct)}</td>
              <td className="muted">session change</td>
            </tr>
          ))}
        </tbody>
      </table>
      </div>
    </Card>
  );
}

const TENORS = ["3m", "2y", "5y", "10y", "30y"];

function bp(v: number | null | undefined): string {
  if (v === null || v === undefined) return "—";
  return `${v > 0 ? "+" : ""}${v} bp`;
}

function MovesYieldsCard({ pack }: { pack: MorningPack }) {
  const m = pack.moves;
  const y = pack.yields;
  if (!m && !y) return null;
  const tenors = y ? [...TENORS.filter((t) => t in y.yields), ...Object.keys(y.yields).filter((t) => !TENORS.includes(t))] : [];
  return (
    <Card
      title="Expected move and yields"
      controls={
        <>
          {y?.session && <span className="card-asof">Treasury curve, {y.session}</span>}
        </>
      }
    >
      {m && (
        <div className="stats-grid">
          <div className="stat-tile">
            <span className="stat-label">SPX weekly expected move</span>
            <span className="stat-value">
              {m.weeklyExpectedMove.pct === null ? "—" : `±${fmt(m.weeklyExpectedMove.pct)}%`}
            </span>
            <span className="stat-label muted">
              {m.weeklyExpectedMove.pct === null
                ? (m.weeklyExpectedMove.reason ?? "unmeasured")
                : `±${fmt(m.weeklyExpectedMove.points, 1)} pts · VIX ${fmt(m.weeklyExpectedMove.vix)} ÷ √52`}
            </span>
          </div>
          <div className="stat-tile">
            <span className="stat-label">SPX realized vol</span>
            <span className="stat-value">{m.realizedVol.pct === null ? "—" : `${fmt(m.realizedVol.pct)}%`}</span>
            <span className="stat-label muted">
              {m.realizedVol.pct === null
                ? (m.realizedVol.reason ?? "unmeasured")
                : `${m.realizedVol.sessions ?? "?"} sessions through ${m.realizedVol.through ?? "—"}`}
            </span>
          </div>
          <div className="stat-tile">
            <span className="stat-label">VIX − realized</span>
            <span className="stat-value">
              {m.vixMinusRealized === null ? "—" : `${m.vixMinusRealized > 0 ? "+" : ""}${fmt(m.vixMinusRealized)} pts`}
            </span>
            <span className="stat-label muted">implied over realized</span>
          </div>
        </div>
      )}
      {y &&
        (y.reason !== null ? (
          <p className="muted">No Treasury curve ({y.reason}).</p>
        ) : (
          <>
            <div className="table-scroll">
            <table className="data-table">
              <thead>
                <tr>
                  <th>Tenor</th>
                  {tenors.map((t) => (
                    <th key={t}>{t}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                <tr>
                  <td>Yield</td>
                  {tenors.map((t) => (
                    <td key={t}>{y.yields[t] === null || y.yields[t] === undefined ? "—" : `${fmt(y.yields[t])}%`}</td>
                  ))}
                </tr>
                <tr>
                  <td>Change</td>
                  {tenors.map((t) => (
                    <td key={t} className="muted">
                      {bp(y.changeBp[t])}
                    </td>
                  ))}
                </tr>
              </tbody>
            </table>
            </div>
            <p className="muted">
              2s10s {bp(y.spread2s10sBp)} · 3m10y {bp(y.spread3m10yBp)} — Treasury par curve, the prior session.
            </p>
          </>
        ))}
    </Card>
  );
}

function count(v: number | null | undefined): string {
  return v === null || v === undefined ? "—" : v.toLocaleString();
}


function HotRows({
  title,
  rows,
}: {
  title: string;
  rows: MorningHotOptionsRow[];
}) {
  // The # is the pack's stored rank, so it travels with its row and sorting by it restores the ranking.
  const sort = useSort();
  const sorted = sortRows(rows, sort, {
    rank: (r) => r.rank,
    symbol: (r) => r.symbol,
    contracts: (r) => r.contracts,
    calls: (r) => r.calls,
    puts: (r) => r.puts,
    pc: (r) => r.putCall,
    rel: (r) => r.relativeVolume,
    customer: (r) => r.customerSidePct,
  });
  return (
    <>
      <h3>{title}</h3>
      <div className="table-scroll">
      <table className="data-table">
        <thead>
          <tr>
            <SortTh label="#" k="rank" sort={sort} />
            <SortTh label="Underlying" k="symbol" sort={sort} />
            <SortTh label="Contracts" k="contracts" sort={sort} />
            <SortTh label="Calls" k="calls" sort={sort} />
            <SortTh label="Puts" k="puts" sort={sort} />
            <SortTh label="P/C" k="pc" sort={sort} />
            <SortTh label="vs avg" k="rel" sort={sort} />
            <SortTh label="Customer sides" k="customer" sort={sort} />
          </tr>
        </thead>
        <tbody>
          {sorted.map((r) => (
            <tr key={r.symbol}>
              <td className="muted">{count(r.rank)}</td>
              <td>
                {r.symbol}
              </td>
              <td>{count(r.contracts)}</td>
              <td>{count(r.calls)}</td>
              <td>{count(r.puts)}</td>
              <td>{fmt(r.putCall)}</td>
              <td className={r.relativeVolume === null ? "muted" : ""}>
                {r.relativeVolume === null ? "—" : `${fmt(r.relativeVolume)}x`}
              </td>
              <td className="muted">{r.customerSidePct === null ? "—" : `${fmt(r.customerSidePct, 1)}%`}</td>
            </tr>
          ))}
        </tbody>
      </table>
      </div>
    </>
  );
}

/** OCC's prior-session option volume, ranked the Hot Options Report's way. The ranking is the
 *  pack's; this only lays it out. */
const HOT_HELP: CardHelpContent = {
  title: "Hot options",
  body: (
    <HelpSection title="Record-only">
      <p>
        OCC cleared volume ranked by contracts. “vs avg” is against the prior sessions the pack names. It feeds no gate and no sizing.
      </p>
    </HelpSection>
  ),
};

function HotOptionsCard({ pack }: { pack: MorningPack }) {
  const h = pack.hotOptions;
  if (!h) return null;
  return (
    <Card
      title="Hot options" flag="record-only" help={HOT_HELP}
      controls={
        <>
          {h.session && <span className="card-asof">OCC cleared volume, {h.session}</span>}
        </>
      }
    >
      {h.reason !== null ? (
        <p className="muted">
          Not measured ({h.reason}
          {h.session ? `, newest file ${h.session}` : ""}).
        </p>
      ) : (
        <>
          <p className="muted">
            {count(h.totalContracts)} contracts across {count(h.underlyings)} underlyings, put/call{" "}
            {fmt(h.totalPutCall)}. “vs avg” is against {h.baselineSessions ?? 0} prior sessions.
            {h.lagSessions ? ` ${h.lagSessions} session(s) behind the prior session — OCC publishes late in the evening, and the morning fetch had not landed it.` : ""}
          </p>
          <HotRows title="Indexes" rows={h.indexes} />
          {h.equities === null ? (
            <p className="muted">Equities and funds not ranked ({h.classification ?? "no directory"}).</p>
          ) : (
            <>
              <HotRows title="Top single-name equities" rows={h.equities} />
              {h.funds && h.funds.length > 0 && <HotRows title="Top funds" rows={h.funds} />}
              {h.unclassified.length > 0 && (
                <p className="muted">Unclassified among the most active: {h.unclassified.join(", ")}.</p>
              )}
            </>
          )}
        </>
      )}
    </Card>
  );
}

function GatesCard({ current }: { current: MorningPack }) {
  return (
    <Card
      title="Gates"
      className="lv-12"
      controls={
        <>
          {current.phase !== null && current.phase.gatesTotal !== null && (
            <span className="card-asof">{current.phase.gatesTotal} declared</span>
          )}
        </>
      }
    >
      {current.gates.length === 0 ? (
        <p className="muted">No gates in this pack.</p>
      ) : (
        <div className="table-scroll">
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
        </div>
      )}
    </Card>
  );
}

const NARRATIVE_HELP: CardHelpContent = {
  title: "About the narrative",
  body: (
    <HelpSection title="Interpretation, not fact">
      <p>The narrative is written from the fact pack on this page; the figures are the pack's, the reading of them is the narrative's.</p>
    </HelpSection>
  ),
};

/** `tabs` is the Reports page's tab strip, rendered inside this page's own title row. Optional so
 *  the page still stands alone if it is ever routed to directly. */
export function MorningPage({ tabs }: { tabs?: ReactNode } = {}) {
  const [session, setSession] = useState<string | undefined>(undefined);
  const { data, isLoading, isError } = useMorningReport(session);
  // The technicals cards and the narrative leave the page when the suite turns their feature off;
  // the fact pack itself is the overview package's and always shows.
  const features = useFeatures();
  const showTechnicals = isFeatureOn("technicals", features);
  const showNarrative = isFeatureOn("morning_narrative", features);

  const current = data?.current ?? null;
  const levels = current?.levels ?? null;
  const sectors = current?.sectors ?? null;
  const latest = data?.sessions[data.sessions.length - 1];
  const sessionFilter = useFilterPanel({
    parts: [session === undefined || session === latest ? "latest session" : `session ${session}`],
    changed: session !== undefined && session !== latest ? 1 : 0,
    reset: () => setSession(undefined),
    children: (
      <FilterGroup title="session" note="the fact pack written before that open">
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

  return (
    <div className="page">
      <div className="page-title-row">
        <h1>Morning report</h1>
        {tabs}
        {current && <span className="chip">{current.session}</span>}
        {current && <span className="card-asof">fact pack v{current.factVersion ?? "?"}</span>}
      </div>

      {isError && <p className="muted">Could not read the overview store.</p>}
      {isLoading && !current && <p className="muted">Reading the overview store…</p>}
      {!isLoading && !isError && !current && (
        <p className="muted">No morning pack has been written yet. The report runs before the open.</p>
      )}

      {current && (
        <div className="pf-layout">
          <Section concept="stand" tag="the verdict" question="What kind of session is it?">
          <PhaseBanner pack={current} />

          <GatesCard current={current} />

          <Card
            title="Scorecard"
            className="lv-12"
            controls={
              <>
                {current.generatedAt !== null && <span className="card-asof">generated {current.generatedAt}</span>}
                {pickSession ? sessionFilter.button : null}
              </>
            }
          >
            {pickSession ? sessionFilter.body : null}
            <div className="stats-grid">
              {SCORECARD.map(({ reading, future }) => scorecardTile(current, reading, future))}
            </div>
          </Card>

          <DeploymentCard pack={current} />
          </Section>

          <Section concept="risk" tag="vol and gamma" question="Where is the risk?">
            <Card
              title={`Gamma levels ${levels?.symbol !== null && levels?.symbol !== undefined ? `· ${levels.symbol}` : ""}`}
              controls={
                levels?.session !== null && levels?.session !== undefined ? <span className="card-asof">as of {levels.session}</span> : null
              }
            >
              {levels === null ? (
                <p className="muted">No levels in this pack.</p>
              ) : (
                <>
                  {/* The levels' spatial relationship is the reading — "just above the flip, short
                      of the call wall" — and a row of tiles makes the reader do that arithmetic.
                      The tiles stay beneath for the exact figures and their basis labels. */}
                  <LevelStrip
                    levels={[
                      { label: "put wall", value: levels.putWall, color: "var(--neg)" },
                      { label: "flip", value: levels.zeroGamma, color: "var(--accent)" },
                      { label: "call wall", value: levels.callWall, color: "var(--pos)" },
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
            </Card>

            <VolRegimeCard pack={current} />
          </Section>

          <Section concept="dir" tag="before the open" question="What is moving before the open?">
          <PremarketCard pack={current} />

          <Card
            title="Sector board"
            controls={
              <>
                {sectors !== null && sectors.measured !== null && (
                  <span className="card-asof">{sectors.measured} of {sectors.board.length} measured</span>
                )}
                {sectors?.strongest && (
                  <span className="chip chip-ok">strongest {sectors.strongest.symbol}</span>
                )}
                {sectors?.weakest && (
                  <span className="chip chip-warn">weakest {sectors.weakest.symbol}</span>
                )}
              </>
            }
          >
            {sectors === null || sectors.board.length === 0 ? (
              <p className="muted">No sector board in this pack.</p>
            ) : (
              <div className="table-scroll">
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
              </div>
            )}
          </Card>

          <MovesYieldsCard pack={current} />
          </Section>

          {showTechnicals && <TechnicalsCards t={data?.technicals} />}

          <Section concept="add" tag="activity" question="Where is the options activity?">
            <HotOptionsCard pack={current} />
          </Section>

          <Section concept="room" tag="calendar" question="What is on the calendar?">
            <CalendarCard pack={current} />
          </Section>

          {!showNarrative ? null : data?.note ? (
            <Section concept="edge" tag="interpretation" question="What does the note say?">
              <Card
                title="Narrative"
                className="review-note"
                help={NARRATIVE_HELP}
              >
                <NoteMarkdown text={data.note} />
              </Card>
            </Section>
          ) : (
            <p className="muted">No narrative for this session.</p>
          )}
        </div>
      )}
    </div>
  );
}
