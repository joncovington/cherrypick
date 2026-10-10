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
/* ---------------------------------------------------------------------------
 * Fixture data — the ONLY thing a real implementation replaces.
 * ------------------------------------------------------------------------- */

const FIXTURE = {
  session: "Wednesday, October 7, 2026",
  entriesFilled: 3,
  hero: {
    net: 254, // gross − exit costs − fees − settlement, whole-position dollars
    gross: 1580, // premium sold, entries summed
    fees: 245,
    settlement: 120,
    openRisk: 480, // worst case still open, the gate's own figure
  },
  /** Net premium kept after fees, as a % of premium sold. */
  capture: { value: 16.1, domain: [-50, 50] as const },
  winRate: { days: { won: 12, of: 16 }, entries: { won: 2, of: 3 } },
  sides: [
    { side: "put", net: -55, credit: 560, stops: 520, decay: 95, avgCredit: 0.6 },
    { side: "call", net: 674, credit: 1020, stops: 216, decay: 130, avgCredit: 0.84 },
  ],
  /** By entry time; a negative bucket renders as a marker, per the reference design. */
  timeBuckets: [
    { time: "13:11", net: -158, note: "manual stop" },
    { time: "14:02", net: 210, note: "credit improved, taken" },
    { time: "15:26", net: 202, note: "eod exit" },
  ],
  entries: [
    { time: "13:11", credit: 505, put: "stopped", call: "protected", net: -158, provisional: true, note: "manual stop" },
    { time: "14:02", credit: 560, put: "closed", call: "closed", net: 210, provisional: false, note: "" },
    { time: "15:26", credit: 515, put: "closed", call: "protected", net: 202, provisional: false, note: "manual" },
  ],
};

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

function NetCard() {
  const { hero } = FIXTURE;
  const drag = hero.fees / hero.gross;
  return (
    <TsCard title="net p&l" aside={<span className="ts-muted">{FIXTURE.session}</span>}>
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

function CaptureGauge() {
  const { value } = FIXTURE.capture;
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

function WinRateArcs() {
  const { days, entries } = FIXTURE.winRate;
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

function BySide() {
  const max = Math.max(...FIXTURE.sides.map((s) => Math.abs(s.net)));
  return (
    <TsCard title="by side" aside={<span className="ts-muted">exits applied, before fees & settlement</span>}>
      {FIXTURE.sides.map((s) => (
        <div className="ts-side" key={s.side}>
          <div className="ts-side-head">
            <span>{s.side}</span>
            <span className={`ts-num ${s.net >= 0 ? "ts-pos" : "ts-neg"}`}>{fmtUsd(s.net)}</span>
          </div>
          <div className="ts-hbar">
            <div
              className={`ts-hbar-fill ${s.net >= 0 ? "ts-pos" : "ts-neg"}`}
              style={{ width: `${((Math.abs(s.net) / max) * 100).toFixed(1)}%` }}
            />
          </div>
          <div className="ts-side-sub">
            credit {fmtUsd(s.credit)} · stops −{s.stops.toLocaleString("en-US")} · decay buybacks −{s.decay.toLocaleString("en-US")} · avg credit {s.avgCredit.toFixed(2)} cr
          </div>
        </div>
      ))}
    </TsCard>
  );
}

/* ---------------------------------------------------------------------------
 * By entry time — a bar per bucket; losses are short markers, per the design.
 * ------------------------------------------------------------------------- */

function ByEntryTime() {
  const max = Math.max(...FIXTURE.timeBuckets.map((b) => Math.abs(b.net)));
  return (
    <TsCard title="by entry time">
      <div className="ts-times">
        {FIXTURE.timeBuckets.map((b) => {
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
 * Entries table — the trade-table standard's columns, cut to what a day summary shows.
 * ------------------------------------------------------------------------- */

function EntriesTable() {
  return (
    <TsCard title="entries" aside={<span className="ts-muted">{FIXTURE.entriesFilled} filled</span>}>
      <table className="ts-table">
        <thead>
          <tr><th>time</th><th className="num">credit</th><th>structure</th><th className="num">net</th></tr>
        </thead>
        <tbody>
          {FIXTURE.entries.map((e) => (
            <tr key={e.time}>
              <td>{e.time}</td>
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
 * Toolbar — range segmented control and day navigation. Fixture-static.
 * ------------------------------------------------------------------------- */

const RANGES = ["day", "week", "month", "year", "all-time"] as const;

function Toolbar() {
  const [range, setRange] = useState<string>("day");
  return (
    <div className="ts-toolbar">
      <h2 className="ts-title">trade summary</h2>
      <div className="ts-seg" role="tablist" aria-label="range">
        {RANGES.map((r) => (
          <button key={r} role="tab" aria-selected={range === r} className={range === r ? "ts-seg-on" : ""} onClick={() => setRange(r)}>
            {r}
          </button>
        ))}
      </div>
      <div className="ts-daynav">
        <button aria-label="previous day">‹</button>
        <span>
          {FIXTURE.session} <span className="ts-muted">· {FIXTURE.entriesFilled} entries filled</span>
        </span>
        <button aria-label="next day">›</button>
      </div>
      <span className="ts-badge ts-badge-warn">prototype — fixture data</span>
    </div>
  );
}

export function MeicSummary() {
  return (
    <div className="ts-root">
      <Toolbar />
      <div className="ts-row ts-row-3">
        <NetCard />
        <CaptureGauge />
        <WinRateArcs />
      </div>
      <div className="ts-row ts-row-2">
        <BySide />
        <ByEntryTime />
      </div>
      <div className="ts-row">
        <EntriesTable />
      </div>
    </div>
  );
}
