/**
 * Trade summary — a PROTOTYPE of the denser, gauge-led summary style (segmented range control,
 * hero net card, premium-capture gauge, win-rate arcs, by-side and by-entry-time charts).
 *
 * ⚠️ EVERY NUMBER ON THIS SLIDE IS FIXTURE DATA. No reader backs it yet; the point is to judge
 * the shape in the real app before building `readers/`. Where the real data will come from,
 * per card (all of it already computed or computable server-side — this component must never
 * derive money itself, per the console's "money is derived on the server" rule):
 *
 * - hero net / gross / fees / settlement — `readers/meic.ts` `meicTradeCash` over a date range
 *   (`readers/dateRange.ts` clause), as the history table's totals chip already does.
 * - capture gauge — net over premium sold; `readers/armCosts.ts` already sums `premium` per arm.
 * - win-rate arcs — settled-entry win counts from the same rows (a win = net > 0, the ledger's
 *   own rule, not a new one).
 * - by side — per-side grouping of entry legs (put wing vs call wing) and their exits; needs a
 *   new reader + meic-mirror test, which is the real work this prototype is scoping.
 * - by entry time — ledger entry timestamps, bucketed; same new reader.
 * - entries table — the trade-table standard columns (time, credit, sides, net), which is why
 *   the columns below mirror the standard's identity: credit − exit − costs = net per row.
 * - by short-call delta / by wing width — meic/sessions' own deep-analytics buckets
 *   (`readMeicDeepAnalytics`'s `byDelta`/`byWing`, bucket SQL verbatim: delta bands on the short
 *   call's |delta| at entry, `CAST(wing_width) || '-wide'`). ERA-scoped like the sessions page
 *   defaults, not the day — the card says so — and trades/win%/avg net are the module's own
 *   three measures; the real reader re-derives avg net per slice (never an average of averages).
 *
 * The "provisional" badge marks a net figure that is a mark, not a settlement — the same
 * mid-is-not-a-fill honesty the Live page uses, until a reader defines it for real.
 *
 * Styling is scoped to `.ts-root` (see styles.css, "Trade summary prototype"): this slide ships
 * its own palette (rounded cards, blue selection, mint/salmon tones) borrowed from a reference
 * design, deliberately at odds with the console's terminal-luxe chrome around it. Scoped so the
 * rest of the console is untouched while the style is under judgement; if adopted, the tokens
 * move outward and the scope is dropped.
 *
 * Charts are hand-rolled SVG sized by their container via `useMeasure` — the package rule
 * (a chart in a grid cell measures itself; scaling is not reflowing). The hero figures here are
 * fixed-width enough not to need it, but the gauges and bars take the measured width where one
 * matters. Colour carries only the sign of a number (pos/neg), never a verdict — the package's
 * tone rule applied to pixels.
 */
import { useState, type ReactNode } from "react";
import { useBoolPref, writePref } from "../../lib/prefs";
import type { MeicScope } from "./MeicTables";

export interface MeicSummaryProps {
  /** The page's scope, from the manifest header (symbol/arm selects). Defaults to every arm and
   *  symbol together; the header narrows. Never re-declared here — one scope per MEIC page. */
  scope?: Partial<MeicScope>;
}
/* ---------------------------------------------------------------------------
 * Fixture data — the ONLY thing a real implementation replaces. Stated twice, ONCE PER SCOPE:
 * `ALL` is the whole day (every arm and symbol together), `PER_ARM` each arm's own slice. The
 * slices are constructed to reconcile — summing the arms' rows equals the unscoped totals —
 * because that identity is what the real reader must keep, and the fixture should prove the
 * scope control while it is judged.
 *
 * The scope itself is the PAGE's (the manifest header's symbol/arm selects), not the slide's:
 * one scope per MEIC page is the existing rule, and a second control would let the summary
 * disagree with the tabs beside it. Default is everything together; the header narrows.
 * ------------------------------------------------------------------------- */

const ALL = {
  session: "Wednesday, October 7, 2026",
  entriesFilled: 3,
  /** Hero net / gross / fees / settlement / open risk — one row per arm in the real reader, so the
   *  arms rail (the paper control's figure beside each) reconciles with the hero to the cent.
   *  Reason taxes are exit costs charged, not a verdict on the arm. */
  hero: {
    net: 254, // gross − exit costs − fees − settlement, whole-position dollars
    gross: 1580, // premium sold, entries summed
    fees: 245,
    settlement: 120,
    openRisk: 480, // worst case still open, the gate's own figure
  },
  /** Net premium kept after fees, as a % of premium sold. */
  capture: { value: 16.1 },
  winRate: { days: { won: 12, of: 16 }, entries: { won: 2, of: 3 } },
  /** Leg outcomes — the mock's "by side" rebuilt on MEIC's leg-pair truth (`ic_spread_legs`): the
   *  credit each wing SOLD for (per-side entry credit is recorded; per-side NET is not — pnl is
   *  the condor's), how many legs ended in each state, and the stop cost the wing charged. This is
   *  the pair the module's margin identity reads (clean = both expired, double-stop = both
   *  stopped), so the card states the pair counts, not a verdict on a wing. */
  sides: [
    {
      side: "put",
      credit: 560, // sum of put_credit × multiplier × qty over the window's trades
      legs: { expired: 9, stopped: 4, settled: 1 },
      stopCost: 520,
    },
    {
      side: "call",
      credit: 1020,
      legs: { expired: 11, stopped: 2, settled: 1 },
      stopCost: 216,
    },
  ],
  /** Leg-pair roll-up — the margin identity's own counts (analytics.py `margin_identity`):
   *  clean = both wings expired, double-stop = both stopped, and the fee bar. Never restated
   *  console-side in the real reader — mirrored from the module, pinned by test. */
  pairs: { trades: 16, cleanPct: 50.0, doubleStopPct: 6.3, breakevenBarPct: 15.5 },
  /** By entry time; a negative bucket renders as a marker, per the reference design. */
  timeBuckets: [
    { time: "13:11", net: -158, note: "manual stop" },
    { time: "14:02", net: 210, note: "credit improved, taken" },
    { time: "15:26", net: 202, note: "eod exit" },
  ],
  /** Evaluations — the refusal ledger (`entry_attempts`), one row per arm-evaluation with its
   *  `outcome`. Filled share is a proportion (a bar), not a gauge; the reason bars carry the WHY
   *  and are sorted descending so the dominant refusal reads first. Bars stay NEUTRAL: standing
   *  aside is a decision, not a loss, and a refusal count has no sign to tone by. */
  evaluations: {
    evaluated: 412,
    filled: 3,
    refused: 409,
    reasons: [
      { outcome: "gate_blocked", n: 222, dominantDetail: "regime_gex_negative" },
      { outcome: "cadence_blocked", n: 109, dominantDetail: null },
      { outcome: "sign_rule_blocked", n: 46, dominantDetail: "call_side_floor" },
      { outcome: "no_candidate", n: 10, dominantDetail: null },
      { outcome: "window_blocked", n: 13, dominantDetail: null },
      { outcome: "no_fill", n: 9, dominantDetail: null },
    ],
    // The "what the market was" half — the question the ledger exists to answer: is a gate
    // refusing the regime it was built to refuse?
    market: "at refusal: GEX− 74% of evaluations · median IV rank 0.41 · session quality mixed",
  },
  arms: [
    { arm: "control", symbol: "NQ", evaluated: 208, filled: 2, refused: 206, dominant: "gate_blocked", net: 42 },
    { arm: "wide", symbol: "NQ", evaluated: 204, filled: 1, refused: 203, dominant: "gate_blocked", net: 212 },
  ],
  entries: [
    { time: "13:11", arm: "control", symbol: "NQ", credit: 505, put: "stopped", call: "protected", net: -158, provisional: true, note: "manual stop" },
    { time: "14:02", arm: "control", symbol: "NQ", credit: 560, put: "closed", call: "closed", net: 210, provisional: false, note: "" },
    { time: "15:26", arm: "wide", symbol: "NQ", credit: 515, put: "closed", call: "protected", net: 202, provisional: false, note: "manual" },
  ],
  /** Signal breakdowns — meic/sessions' deep-analytics blocks, era-scoped like that page's
   *  default. Trades/win%/avg net are the module's own three measures per bucket; avg net is
   *  re-derived from every row in the bucket, never an average of averages. A real day can leave
   *  a band empty; a bar can hold at zero. */
  delta: [
    { bucket: "<0.10", trades: 18, sessions: 7, winPct: 66.7, avgNet: 35.5 },
    { bucket: "0.10-0.15", trades: 42, sessions: 9, winPct: 69.0, avgNet: 28.1 },
    { bucket: "0.15-0.20", trades: 11, sessions: 4, winPct: 45.5, avgNet: -12.3 },
    { bucket: ">=0.20", trades: 6, sessions: 3, winPct: 33.3, avgNet: -41.0 },
  ],
  wing: [
    { bucket: "50-wide", trades: 52, sessions: 9, winPct: 71.2, avgNet: 31.0 },
    { bucket: "75-wide", trades: 14, sessions: 5, winPct: 50.0, avgNet: -5.2 },
    { bucket: "100-wide", trades: 11, sessions: 4, winPct: 45.5, avgNet: -12.3 },
  ],
  weekday: [
    { bucket: "Mon", trades: 19, sessions: 3, winPct: 68.4, avgNet: 22.4 },
    { bucket: "Tue", trades: 21, sessions: 3, winPct: 61.9, avgNet: 18.9 },
    { bucket: "Wed", trades: 15, sessions: 3, winPct: 73.3, avgNet: 41.2 },
    { bucket: "Thu", trades: 12, sessions: 3, winPct: 58.3, avgNet: 9.6 },
    { bucket: "Fri", trades: 10, sessions: 4, winPct: 40.0, avgNet: -14.8 },
  ],
};

/** The per-arm slice — what the real reader returns for one arm's rows of the ledger. Same shape
 *  as `ALL` (one fewer level: no `arms` list), constructed so sums across arms equal `ALL`. */
type Slice = Omit<typeof ALL, "arms">;
const PER_ARM: Record<string, Slice> = {
  control: {
    ...ALL,
    hero: { net: 42, gross: 790, fees: 122.5, settlement: 60, openRisk: 240 },
    capture: { value: 4.3 },
    winRate: { days: { won: 5, of: 16 }, entries: { won: 2, of: 3 } },
    sides: [
      { side: "put", credit: 280, legs: { expired: 4, stopped: 3, settled: 0 }, stopCost: 520 },
      { side: "call", credit: 510, legs: { expired: 5, stopped: 1, settled: 1 }, stopCost: 216 },
    ],
    timeBuckets: [
      { time: "13:11", net: -158, note: "manual stop" },
      { time: "14:02", net: 210, note: "credit improved, taken" },
    ],
    evaluations: {
      evaluated: 208,
      filled: 2,
      refused: 206,
      reasons: [
        { outcome: "gate_blocked", n: 111, dominantDetail: "regime_gex_negative" },
        { outcome: "cadence_blocked", n: 55, dominantDetail: null },
        { outcome: "sign_rule_blocked", n: 23, dominantDetail: "call_side_floor" },
        { outcome: "no_candidate", n: 5, dominantDetail: null },
        { outcome: "window_blocked", n: 7, dominantDetail: null },
        { outcome: "no_fill", n: 5, dominantDetail: null },
      ],
      market: "at refusal: GEX− 74% of evaluations · median IV rank 0.41 · session quality mixed",
    },
    entries: [
      { time: "13:11", arm: "control", symbol: "NQ", credit: 505, put: "stopped", call: "protected", net: -158, provisional: true, note: "manual stop" },
      { time: "14:02", arm: "control", symbol: "NQ", credit: 560, put: "closed", call: "closed", net: 210, provisional: false, note: "" },
    ],
    delta: [
      { bucket: "<0.10", trades: 9, sessions: 5, winPct: 66.7, avgNet: 21.4 },
      { bucket: "0.10-0.15", trades: 21, sessions: 6, winPct: 71.4, avgNet: 18.2 },
      { bucket: "0.15-0.20", trades: 6, sessions: 2, winPct: 50.0, avgNet: -8.1 },
      { bucket: ">=0.20", trades: 3, sessions: 2, winPct: 33.3, avgNet: -20.5 },
    ],
    wing: [
      { bucket: "50-wide", trades: 26, sessions: 6, winPct: 73.1, avgNet: 24.7 },
      { bucket: "75-wide", trades: 7, sessions: 3, winPct: 57.1, avgNet: -3.1 },
      { bucket: "100-wide", trades: 6, sessions: 2, winPct: 50.0, avgNet: -6.2 },
    ],
    weekday: [
      { bucket: "Mon", trades: 10, sessions: 2, winPct: 70.0, avgNet: 12.1 },
      { bucket: "Tue", trades: 11, sessions: 2, winPct: 63.6, avgNet: 10.3 },
      { bucket: "Wed", trades: 8, sessions: 2, winPct: 75.0, avgNet: 30.6 },
      { bucket: "Thu", trades: 6, sessions: 2, winPct: 66.7, avgNet: 6.4 },
      { bucket: "Fri", trades: 5, sessions: 2, winPct: 40.0, avgNet: -9.2 },
    ],
  },
  wide: {
    ...ALL,
    hero: { net: 212, gross: 790, fees: 122.5, settlement: 60, openRisk: 240 },
    capture: { value: 26.8 },
    winRate: { days: { won: 7, of: 16 }, entries: { won: 1, of: 3 } },
    sides: [
      { side: "put", credit: 280, legs: { expired: 5, stopped: 1, settled: 1 }, stopCost: 0 },
      { side: "call", credit: 510, legs: { expired: 6, stopped: 1, settled: 0 }, stopCost: 216 },
    ],
    timeBuckets: [{ time: "15:26", net: 202, note: "eod exit" }],
    evaluations: {
      evaluated: 204,
      filled: 1,
      refused: 203,
      reasons: [
        { outcome: "gate_blocked", n: 111, dominantDetail: "regime_gex_negative" },
        { outcome: "cadence_blocked", n: 54, dominantDetail: null },
        { outcome: "sign_rule_blocked", n: 23, dominantDetail: "call_side_floor" },
        { outcome: "no_candidate", n: 5, dominantDetail: null },
        { outcome: "window_blocked", n: 6, dominantDetail: null },
        { outcome: "no_fill", n: 4, dominantDetail: null },
      ],
      market: "at refusal: GEX− 74% of evaluations · both arms share one market, so the context repeats",
    },
    entries: [
      { time: "15:26", arm: "wide", symbol: "NQ", credit: 515, put: "closed", call: "protected", net: 202, provisional: false, note: "manual" },
    ],
    delta: [
      { bucket: "<0.10", trades: 9, sessions: 4, winPct: 66.7, avgNet: 49.6 },
      { bucket: "0.10-0.15", trades: 21, sessions: 5, winPct: 66.7, avgNet: 38.0 },
      { bucket: "0.15-0.20", trades: 5, sessions: 2, winPct: 40.0, avgNet: -16.5 },
      { bucket: ">=0.20", trades: 3, sessions: 1, winPct: 33.3, avgNet: -61.5 },
    ],
    wing: [
      { bucket: "50-wide", trades: 26, sessions: 5, winPct: 69.2, avgNet: 37.3 },
      { bucket: "75-wide", trades: 7, sessions: 2, winPct: 42.9, avgNet: -7.3 },
      { bucket: "100-wide", trades: 5, sessions: 2, winPct: 40.0, avgNet: -18.4 },
    ],
    weekday: [
      { bucket: "Mon", trades: 9, sessions: 1, winPct: 66.7, avgNet: 32.7 },
      { bucket: "Tue", trades: 10, sessions: 1, winPct: 60.0, avgNet: 27.5 },
      { bucket: "Wed", trades: 7, sessions: 1, winPct: 71.4, avgNet: 51.8 },
      { bucket: "Thu", trades: 6, sessions: 1, winPct: 50.0, avgNet: 12.8 },
      { bucket: "Fri", trades: 5, sessions: 2, winPct: 40.0, avgNet: -20.4 },
    ],
  },
};

/** The slice the scope names: the arm's own rows, or all of them together. Symbol narrowing joins
 *  the real reader the day MEIC trades a second symbol; the fixture states one. */
function fixtureForScope(scope: Partial<MeicScope> | undefined): Slice {
  if (scope?.profile != null && scope.profile !== "") {
    const arm = PER_ARM[scope.profile];
    if (arm) return arm;
  }
  return ALL;
}


/* `+$1,580.00` — signed, grouped, whole-position dollars. The summary's own display format for
 * the hero and charts; the entries table keeps the suite's `fmtCash` vocabulary. */
function fmtUsd(v: number): string {
  const sign = v < 0 ? "-" : "+";
  return `${sign}$${Math.abs(v).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

/* ---------------------------------------------------------------------------
 * Shared bits
 * ------------------------------------------------------------------------- */

function TsCard({ title, aside, children }: { title: string; aside?: ReactNode; children: ReactNode }) {
  return (
    <section className="ts-card">
      <header className="ts-card-head">
        <h3>{title}</h3>
        {aside}
      </header>
      {children}
    </section>
  );
}

/* ---------------------------------------------------------------------------
 * Hero net card
 * ------------------------------------------------------------------------- */

function NetCard({ slice }: { slice: Slice }) {
  const { hero } = slice;
  const drag = hero.fees / hero.gross;
  return (
    <TsCard title="net p&l" aside={<span className="ts-muted">{slice.session}</span>}>
      <div className={`ts-hero ${hero.net >= 0 ? "ts-pos" : "ts-neg"}`}>{fmtUsd(hero.net)}</div>
      <dl className="ts-kv">
        <div><dt>gross captured</dt><dd className="ts-pos">{fmtUsd(hero.gross)}</dd></div>
        <div><dt>fees</dt><dd className="ts-neg">−{fmtUsd(hero.fees).slice(1)}</dd></div>
        <div><dt>settlement</dt><dd className="ts-neg">−{fmtUsd(hero.settlement).slice(1)}</dd></div>
        <div><dt>open risk</dt><dd>{fmtUsd(hero.openRisk)}</dd></div>
        <div><dt>fee drag</dt><dd>{(drag * 100).toFixed(1)}%</dd></div>
      </dl>
    </TsCard>
  );
}

/* ---------------------------------------------------------------------------
 * Premium-capture gauge — semicircle, needle at the value, arc filling from 0.
 * Angles run 0° (left) to 180° (right); the domain's zero sits at 90°.
 * ------------------------------------------------------------------------- */

const G = { min: -50, max: 50, cx: 90, cy: 84, r: 70, stroke: 13 };

function gpolar(r: number, theta: number): [number, number] {
  const rad = (theta * Math.PI) / 180;
  return [G.cx - r * Math.cos(rad), G.cy - r * Math.sin(rad)];
}

function gaugeArc(a: number, b: number): string {
  const [x1, y1] = gpolar(G.r, a);
  const [x2, y2] = gpolar(G.r, b);
  return `M ${x1.toFixed(2)} ${y1.toFixed(2)} A ${G.r} ${G.r} 0 0 1 ${x2.toFixed(2)} ${y2.toFixed(2)}`;
}

function thetaOf(v: number): number {
  const t = Math.min(Math.max(v, G.min), G.max);
  return ((t - G.min) / (G.max - G.min)) * 180;
}

function CaptureGauge({ slice }: { slice: Slice }) {
  const { value } = slice.capture;
  const zero = thetaOf(0);
  const at = thetaOf(value);
  const [from, to] = value >= 0 ? [zero, at] : [at, zero];
  const [nx, ny] = gpolar(G.r - 18, at);
  return (
    <TsCard title="premium capture">
      <svg className="ts-gauge" viewBox="0 0 180 104" role="img" aria-label={`capture ${value}%`}>
        <path d={gaugeArc(0, 180)} className="ts-track" strokeWidth={G.stroke} fill="none" strokeLinecap="round" />
        <path d={gaugeArc(from, to)} className={value >= 0 ? "ts-arc-pos" : "ts-arc-neg"} strokeWidth={G.stroke} fill="none" strokeLinecap="round" />
        <line x1={G.cx} y1={G.cy} x2={nx} y2={ny} className="ts-needle" strokeWidth={2} />
        <circle cx={G.cx} cy={G.cy} r={3.5} className="ts-needle" />
        <text x={8} y={100} className="ts-tick">{G.min}</text>
        <text x={168} y={100} className="ts-tick">{G.max}</text>
      </svg>
      <div className={`ts-gauge-value ${value >= 0 ? "ts-pos" : "ts-neg"}`}>{value.toFixed(1)}%</div>
      <p className="ts-muted">of premium sold kept after fees</p>
    </TsCard>
  );
}

/* ---------------------------------------------------------------------------
 * Win-rate arcs — two concentric radial bars, outer = days, inner = entries.
 * ------------------------------------------------------------------------- */

function Ring({ r, fraction, className }: { r: number; fraction: number; className: string }) {
  const c = 2 * Math.PI * r;
  return (
    <>
      <circle cx={70} cy={70} r={r} className="ts-track" strokeWidth={11} fill="none" />
      <circle
        cx={70} cy={70} r={r}
        className={className}
        strokeWidth={11} fill="none" strokeLinecap="round"
        strokeDasharray={`${(fraction * c).toFixed(2)} ${c.toFixed(2)}`}
        transform="rotate(-90 70 70)"
      />
    </>
  );
}

function WinRateArcs({ slice }: { slice: Slice }) {
  const { days, entries } = slice.winRate;
  const dayPct = (days.won / days.of) * 100;
  const entryPct = (entries.won / entries.of) * 100;
  return (
    <TsCard title="win rate">
      <div className="ts-rings">
        <svg viewBox="0 0 140 140" role="img" aria-label={`days ${dayPct}%, entries ${entryPct}%`}>
          <Ring r={54} fraction={dayPct / 100} className="ts-ring-days" />
          <Ring r={36} fraction={entryPct / 100} className="ts-ring-entries" />
        </svg>
        <div className="ts-rings-center">
          <span className="ts-pos">{dayPct.toFixed(0)}%</span>
          <span className="ts-muted">days</span>
        </div>
      </div>
      <dl className="ts-legend">
        <div><span className="ts-swatch ts-ring-days" /> days {days.won}/{days.of}</div>
        <div><span className="ts-swatch ts-ring-entries" /> entries {entries.won}/{entries.of}</div>
      </dl>
    </TsCard>
  );
}

/* ---------------------------------------------------------------------------
 * By side — put vs call, horizontal bars on a shared scale.
 * The header states what its net covers (exits applied, fees not) so the card
 * reconciles with the hero rather than silently differing from it.
 * ------------------------------------------------------------------------- */

function BySide({ slice }: { slice: Slice }) {
  const max = Math.max(...slice.sides.map((s) => s.credit));
  const p = slice.pairs;
  return (
    <TsCard
      title="leg outcomes"
      aside={
        <span className="ts-muted">
          {p.trades} trades · clean {p.cleanPct.toFixed(1)}% · double-stop {p.doubleStopPct.toFixed(1)}%
        </span>
      }
    >
      {slice.sides.map((s) => (
        <div className="ts-side" key={s.side}>
          <div className="ts-side-head">
            <span>{s.side}</span>
            <span className="ts-num ts-pos">credit {fmtUsd(s.credit)}</span>
          </div>
          <div className="ts-hbar">
            <div
              className="ts-hbar-fill ts-pos"
              style={{ width: `${((s.credit / max) * 100).toFixed(1)}%` }}
            />
          </div>
          <div className="ts-side-sub">
            {s.legs.expired} expired · {s.legs.stopped} stopped · {s.legs.settled} settled · stop cost −{s.stopCost.toLocaleString("en-US")}
          </div>
        </div>
      ))}
      <div className="ts-side-sub">
        fee bar {p.breakevenBarPct.toFixed(1)}% of credit — avg fee over avg credit sold, the module's breakeven identity
      </div>
    </TsCard>
  );
}

/* ---------------------------------------------------------------------------
 * By entry time — a bar per bucket; losses are short markers, per the design.
 * ------------------------------------------------------------------------- */

function ByEntryTime({ slice }: { slice: Slice }) {
  const max = Math.max(...slice.timeBuckets.map((b) => Math.abs(b.net)));
  return (
    <TsCard title="by entry time">
      <div className="ts-times">
        {slice.timeBuckets.map((b) => {
          const h = `${((Math.abs(b.net) / max) * 100).toFixed(1)}%`;
          return (
            <div className="ts-time" key={b.time} title={`${b.time} — ${fmtUsd(b.net)} ${b.note}`}>
              <div className="ts-time-area">
                {b.net >= 0 ? (
                  <div className="ts-vbar ts-pos" style={{ height: h }} />
                ) : (
                  <div className="ts-vbar ts-neg ts-marker" style={{ height: h }} />
                )}
              </div>
              <div className="ts-time-label">{b.time}</div>
              <div className="ts-time-sub">{b.net < 0 ? "manual" : b.note}</div>
            </div>
          );
        })}
      </div>
    </TsCard>
  );
}

/* ---------------------------------------------------------------------------
 * Entries table — the trade-table standard's columns, cut to what a day summary shows. Identity
 * columns first (arm, symbol — MEIC is parallel arms over symbols, so identity IS the summary).
 * ------------------------------------------------------------------------- */

function EntriesTable({ slice }: { slice: Slice }) {
  return (
    <TsCard title="entries" aside={<span className="ts-muted">{slice.entriesFilled} filled</span>}>
      <table className="ts-table">
        <thead>
          <tr><th>time</th><th>arm</th><th>sym</th><th className="num">credit</th><th>structure</th><th className="num">net</th></tr>
        </thead>
        <tbody>
          {slice.entries.map((e) => (
            <tr key={`${e.time}-${e.arm}`}>
              <td>{e.time}</td>
              <td>{e.arm}</td>
              <td>{e.symbol}</td>
              <td className="num ts-pos">{fmtUsd(e.credit)}</td>
              <td className="ts-muted">put {e.put} · call {e.call}</td>
              <td className="num">
                <span className={e.net >= 0 ? "ts-pos" : "ts-neg"}>{fmtUsd(e.net)}</span>
                {e.provisional && <span className="ts-badge ts-badge-warn">provisional</span>}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </TsCard>
  );
}

/* ---------------------------------------------------------------------------
 * Evaluations — the refusal ledger, told twice. A thin funnel bar for the top line (filled share
 * is a proportion of a whole: a bar, not a gauge — a gauge implies a natural scale there isn't),
 * then one horizontal bar per `entry_attempts.outcome`, sorted descending, each labelled with its
 * `block_detail` so "dominant refusal" is readable, not computed by the reader's eye. Bars are
 * NEUTRAL — no tone — because a refusal count has no sign: standing aside is a decision, not a
 * loss, and a gate count must not read as a verdict. The "what the market was" line carries the
 * ledger's other half (GEX sign at refusal, IV rank, session quality), which is the question the
 * ledger exists to answer: is a gate refusing the regime it was built to refuse?
 * ------------------------------------------------------------------------- */

function Evaluations({ slice }: { slice: Slice }) {
  const ev = slice.evaluations;
  const maxN = Math.max(...ev.reasons.map((r) => r.n));
  const passPct = (ev.filled / ev.evaluated) * 100;
  return (
    <TsCard title="evaluations">
      <div className="ts-funnel-head">
        <span>
          <strong className="ts-num">{ev.filled.toLocaleString("en-US")}</strong> filled of{" "}
          <strong className="ts-num">{ev.evaluated.toLocaleString("en-US")}</strong> evaluated
        </span>
        <span className="ts-muted">{ev.refused.toLocaleString("en-US")} refused</span>
      </div>
      <div className="ts-hbar ts-funnel">
        <div className="ts-funnel-fill" style={{ width: `${passPct.toFixed(2)}%` }} />
      </div>
      <div className="ts-refusal-bars">
        {[...ev.reasons].sort((a, b) => b.n - a.n).map((r) => (
          <div className="ts-refusal" key={r.outcome} title={r.dominantDetail ?? r.outcome}>
            <div className="ts-refusal-head">
              <span>{r.outcome.replace(/_/g, " ")}</span>
              <span className="ts-num">{r.n.toLocaleString("en-US")}</span>
            </div>
            <div className="ts-hbar">
              <div className="ts-hbar-fill ts-neutral" style={{ width: `${((r.n / maxN) * 100).toFixed(1)}%` }} />
            </div>
            {r.dominantDetail && <div className="ts-side-sub">{r.dominantDetail}</div>}
          </div>
        ))}
      </div>
      <p className="ts-muted ts-market">{ev.market}</p>
    </TsCard>
  );
}

/* ---------------------------------------------------------------------------
 * Signal breakdown — meic/sessions' deep-analytics block in the summary's bar style. The
 * module's own buckets (delta bands on the short call's |delta| at entry, `CAST(wing_width)
 * || '-wide'`), the module's own three measures (trades, win %, avg net) — rendered as one
 * horizontal bar per bucket, scaled by trades, with the money measure beside it. ERA-scoped
 * like the sessions page's default, so the aside says the window rather than the day; a day
 * with three trades would make every band a coin toss.
 * ------------------------------------------------------------------------- */

interface BreakdownRow {
  bucket: string;
  trades: number;
  sessions: number;
  winPct: number | null;
  avgNet: number | null;
}

function SignalBreakdown({
  title,
  rows,
  scopeNote,
}: {
  title: string;
  rows: BreakdownRow[];
  scopeNote: string;
}) {
  const max = Math.max(...rows.map((r) => r.trades), 1);
  return (
    <TsCard title={title} aside={<span className="ts-muted">{scopeNote}</span>}>
      {rows.map((r) => (
        <div className="ts-side" key={r.bucket} title={`${r.trades} trades over ${r.sessions} sessions — avg net per trade ${r.avgNet === null ? "n/r" : fmtUsd(r.avgNet)}`}>
          <div className="ts-side-head">
            <span className="ts-num">{r.bucket}</span>
            <span className="ts-num ts-muted">{r.trades} trades</span>
          </div>
          <div className="ts-hbar">
            <div
              className={`ts-hbar-fill ${r.avgNet === null ? "ts-neutral" : r.avgNet >= 0 ? "ts-pos" : "ts-neg"}`}
              style={{ width: `${((r.trades / max) * 100).toFixed(1)}%` }}
            />
          </div>
          <div className="ts-side-sub">
            win {r.winPct === null ? "n/r" : `${r.winPct.toFixed(1)}%`} · avg net {r.avgNet === null ? "n/r" : fmtUsd(r.avgNet)}/trade
          </div>
        </div>
      ))}
    </TsCard>
  );
}

/* ---------------------------------------------------------------------------
 * By arm — one row per arm, its own day across the slide's other cards. The hero is one row of
 * this table; the reason taxes are exit costs charged, never a verdict on the arm.
 * ------------------------------------------------------------------------- */

function ByArm() {
  return (
    <TsCard title="by arm" aside={<span className="ts-muted">every arm — the comparison card does not narrow</span>}>
      <table className="ts-table">
        <thead>
          <tr>
            <th>arm</th>
            <th>sym</th>
            <th className="num">eval</th>
            <th className="num">filled</th>
            <th>dominant refusal</th>
            <th className="num">net</th>
          </tr>
        </thead>
        <tbody>
          {ALL.arms.map((a) => (
            <tr key={a.arm}>
              <td>{a.arm}</td>
              <td>{a.symbol}</td>
              <td className="num">{a.evaluated.toLocaleString("en-US")}</td>
              <td className="num">{a.filled}</td>
              <td className="ts-muted">{a.dominant.replace(/_/g, " ")}</td>
              <td className="num">
                <span className={a.net >= 0 ? "ts-pos" : "ts-neg"}>{fmtUsd(a.net)}</span>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </TsCard>
  );
}

/* ---------------------------------------------------------------------------
 * Toolbar — range segmented control and day navigation. Fixture-static.
 * ------------------------------------------------------------------------- */

const RANGES = ["day", "week", "month", "year", "all-time"] as const;

function Toolbar({ slice }: { slice: Slice }) {
  const [range, setRange] = useState<string>("day");
  const [dayOffset, setDayOffset] = useState(0);
  // The light variant is a choice, persisted like every console pref (mirror first render, server
  // copy follows); default dark, never `prefers-color-scheme` — a deliberately dark theme must not
  // flip because the OS said so.
  const light = useBoolPref("meicSummaryLight");
  return (
    <div className="ts-toolbar">
      <h2 className="ts-title">trade summary</h2>
      <div className="ts-seg" role="tablist" aria-label="range">
        {RANGES.map((r) => (
          <button
            key={r}
            role="tab"
            aria-selected={range === r}
            className={range === r ? "ts-seg-on" : ""}
            onClick={() => setRange(r)}
            title={r === "day" ? undefined : "fixture limit: the prototype renders one day — the reader wires this range"}
          >
            {r}
          </button>
        ))}
      </div>
      <div className="ts-daynav">
        <button
          aria-label="previous day"
          title="fixture limit: day navigation is not wired — the reader moves the window"
          onClick={() => setDayOffset((d) => d - 1)}
        >
          ‹
        </button>
        <span>
          {dayOffset === 0 ? slice.session : `day −${-dayOffset} (fixture)`} <span className="ts-muted">· {slice.entriesFilled} entries filled</span>
        </span>
        <button
          aria-label="next day"
          title="fixture limit: day navigation is not wired — the reader moves the window"
          onClick={() => setDayOffset((d) => Math.min(0, d + 1))}
        >
          ›
        </button>
      </div>
      <div className="ts-seg ts-theme-seg" role="group" aria-label="theme">
        <button className={!light ? "ts-seg-on" : ""} onClick={() => writePref("meicSummaryLight", false)}>dark</button>
        <button className={light ? "ts-seg-on" : ""} onClick={() => writePref("meicSummaryLight", true)}>light</button>
      </div>
      <span className="ts-badge ts-badge-warn">prototype — fixture data</span>
    </div>
  );
}

export function MeicSummary({ scope }: MeicSummaryProps) {
  const light = useBoolPref("meicSummaryLight");
  const slice = fixtureForScope(scope);
  return (
    <div className={light ? "ts-root ts-light" : "ts-root"}>
      <Toolbar slice={slice} />
      {/* Rows group by the question they answer: money; the trade as built and how it ended
          (leg outcomes + the two geometry cards beside it); the gate's ledger and the arms; when
          we trade (the day's clock beside the era's week); then the ledger itself. */}
      <div className="ts-row ts-row-3">
        <NetCard slice={slice} />
        <CaptureGauge slice={slice} />
        <WinRateArcs slice={slice} />
      </div>
      <div className="ts-row ts-row-3">
        <BySide slice={slice} />
        <SignalBreakdown title="by wing width" rows={slice.wing} scopeNote="era, not the day" />
        <SignalBreakdown title="by short-call delta" rows={slice.delta} scopeNote="era, not the day" />
      </div>
      <div className="ts-row ts-row-2">
        <Evaluations slice={slice} />
        <ByArm />
      </div>
      <div className="ts-row ts-row-2">
        <ByEntryTime slice={slice} />
        <SignalBreakdown title="by weekday" rows={slice.weekday} scopeNote="era, not the day" />
      </div>
      {/* Entries is full width UNDER the signal blocks — the real reader returns every filled entry
          of the window, a long row of data that a third of a grid cell would cramp. */}
      <div className="ts-row">
        <EntriesTable slice={slice} />
      </div>
    </div>
  );
}
