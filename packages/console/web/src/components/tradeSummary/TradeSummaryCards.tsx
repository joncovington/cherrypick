import type { ReactNode } from "react";

/**
 * The trade-summary page's card primitives, extracted from the MEIC prototype
 * (`pages/Meic/SummarySlide.tsx`) when the surface went suite-wide (2026-10-11, Reports'
 * summary page). The prototype stays the module-local deep view; both render from these, so a
 * styling fix or a fix to the gauge geometry lands once. Everything here draws from a payload
 * the server already computed — a component that derived money would be the
 * `services/report.ts` mistake again — and colour carries sign only: never a green/red rule of
 * its own beyond the value's own sign.
 *
 * The styles are the prototype's `.ts-*` scope in `styles.css` — both pages wrap in `.ts-root`,
 * so one stylesheet serves both.
 */

export const RANGES = ["day", "week", "month", "year", "all"] as const;
export type Range = (typeof RANGES)[number];

/** `+$1,580.00` — signed, grouped, whole-position dollars. The summary's own display format. */
export function fmtUsd(v: number): string {
  const sign = v < 0 ? "-" : "+";
  return `${sign}$${Math.abs(v).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

/** The window's session label, as the header's day select says it. An ISO date becomes
 * "Wednesday, October 7, 2026"; an open window (week/month/…) shows the reader's own label. */
export function sessionLabel(iso: string): string {
  const d = new Date(`${iso}T12:00:00Z`);
  return d.toLocaleDateString("en-US", { weekday: "long", year: "numeric", month: "long", day: "numeric", timeZone: "UTC" });
}

/** The label for a window: a day names its session, anything else shows its own label. */
export function windowLabel(label: string | undefined, from: string | null | undefined): string {
  if (label === "day" && from) return sessionLabel(from);
  return label ?? "…";
}

export function TsCard({ title, aside, children }: { title: string; aside?: ReactNode; children: ReactNode }) {
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

/* A card whose payload has nothing for it. Says why, draws nothing — a zero would read as
 * "traded and broke even", which is a different claim from "no rows in this window". */
export function TsEmpty({ what }: { what: string }) {
  return (
    <section className="ts-card">
      <header className="ts-card-head">
        <h3>{what}</h3>
      </header>
      <p className="ts-empty">no rows in this window</p>
    </section>
  );
}

/* ---------------------------------------------------------------------------
 * Gauge geometry — angles run 0° (left) to 180° (right); the domain's zero sits mid-rail. The
 * needle clamps to the domain; the label always shows the UNclamped value, so a big day pins
 * the rail without misreporting its own percentage. Two domains use this: capture's −50..+50
 * and return-on-risk's −100..+100 (a defined-risk position's return lives inside its own
 * worst case, so the scale ends where the position can).
 * ------------------------------------------------------------------------- */

export interface GaugeDomain {
  min: number;
  max: number;
  cx: number;
  cy: number;
  r: number;
  stroke: number;
}

export const GAUGE: GaugeDomain = { min: -50, max: 50, cx: 90, cy: 84, r: 70, stroke: 13 };
export const ROR_DOMAIN: GaugeDomain = { min: -100, max: 100, cx: 90, cy: 84, r: 70, stroke: 13 };

function gpolar(g: GaugeDomain, r: number, theta: number): [number, number] {
  const rad = (theta * Math.PI) / 180;
  return [g.cx - r * Math.cos(rad), g.cy - r * Math.sin(rad)];
}

function gaugeArc(g: GaugeDomain, a: number, b: number): string {
  const [x1, y1] = gpolar(g, g.r, a);
  const [x2, y2] = gpolar(g, g.r, b);
  return `M ${x1.toFixed(2)} ${y1.toFixed(2)} A ${g.r} ${g.r} 0 0 1 ${x2.toFixed(2)} ${y2.toFixed(2)}`;
}

function thetaOf(g: GaugeDomain, v: number): number {
  const t = Math.min(Math.max(v, g.min), g.max);
  return ((t - g.min) / (g.max - g.min)) * 180;
}

/** The arc pair every gauge draws: track, signed fill from centre, needle, end ticks. */
function GaugeSvg({ g, value, ticks, aria }: { g: GaugeDomain; value: number; ticks: [string, string]; aria: string }) {
  const zero = thetaOf(g, 0);
  const at = thetaOf(g, value);
  const [from, to] = value >= 0 ? [zero, at] : [at, zero];
  const [nx, ny] = gpolar(g, g.r - 18, at);
  return (
    <svg className="ts-gauge" viewBox="0 0 180 104" role="img" aria-label={aria}>
      <path d={gaugeArc(g, 0, 180)} className="ts-track" strokeWidth={g.stroke} fill="none" strokeLinecap="round" />
      <path d={gaugeArc(g, from, to)} className={value >= 0 ? "ts-arc-pos" : "ts-arc-neg"} strokeWidth={g.stroke} fill="none" strokeLinecap="round" />
      <line x1={g.cx} y1={g.cy} x2={nx} y2={ny} className="ts-needle" strokeWidth={2} />
      <circle cx={g.cx} cy={g.cy} r={3.5} className="ts-needle" />
      <text x={8} y={100} className="ts-tick">{ticks[0]}</text>
      <text x={160} y={100} className="ts-tick">{ticks[1]}</text>
    </svg>
  );
}

/* ---------------------------------------------------------------------------
 * Hero net card — the window's money row: net, its parts, fee drag. Signed cash flow on every
 * line; `n/r` where a part was never recorded (a zero fee is a claim, an n/r is an admission).
 * ------------------------------------------------------------------------- */

export function NetMoneyCard({
  net,
  gross,
  fees,
  settlement,
  openRisk,
  label,
}: {
  net: number;
  gross: number;
  fees: number | null;
  settlement: number | null;
  openRisk: number | null;
  label: string;
}) {
  const drag = gross > 0 && fees !== null ? fees / gross : null;
  return (
    <TsCard title="net p&l" aside={<span className="ts-muted">{label}</span>}>
      <div className={`ts-hero ${net >= 0 ? "ts-pos" : "ts-neg"}`}>{fmtUsd(net)}</div>
      <dl className="ts-kv">
        <div><dt>gross captured</dt><dd className="ts-pos">{fmtUsd(gross)}</dd></div>
        {fees !== null && <div><dt>fees</dt><dd className="ts-neg">−{fmtUsd(fees).slice(1)}</dd></div>}
        {settlement !== null && <div><dt>settlement</dt><dd className="ts-neg">−{fmtUsd(settlement).slice(1)}</dd></div>}
        {openRisk !== null && <div><dt>open risk</dt><dd>{fmtUsd(openRisk)}</dd></div>}
        <div><dt>fee drag</dt><dd>{drag === null ? "n/r" : `${(drag * 100).toFixed(1)}%`}</dd></div>
      </dl>
    </TsCard>
  );
}

/* The capture gauge: of premium sold, kept after fees. Rendered only when the module's rows
 * carry a max profit to compare against — the reader's own gate (a partial denominator shows
 * as n/r rather than as a ratio over an invisible subset). */
export function CaptureGaugeCard({
  capture,
  trades,
}: {
  capture: { value: number | null; n: number } | null;
  trades: number;
}) {
  if (capture === null || capture.value === null) {
    // Rows traded but no comparable max profit is a different statement from an empty window.
    return trades > 0 ? (
      <TsCard title="premium capture">
        <p className="ts-empty">n/r — this module's rows record no max profit to compare against</p>
      </TsCard>
    ) : (
      <TsEmpty what="premium capture" />
    );
  }
  return (
    <TsCard title="premium capture">
      <GaugeSvg g={GAUGE} value={capture.value} ticks={["−50", "+50"]} aria={`capture ${capture.value.toFixed(1)}%`} />
      <div className={`ts-gauge-value ${capture.value >= 0 ? "ts-pos" : "ts-neg"}`}>{capture.value.toFixed(1)}%</div>
      <p className="ts-muted">of premium sold kept after fees</p>
    </TsCard>
  );
}

/* The return-on-risk gauge — the capture gauge's sibling at ±100. Beside the value, the
 * denominator that made it: the window's capital, and the settled-row coverage, so "9/10" is
 * never read as "10/10". `capitalLabel` says what the denominator IS per module ("capital at
 * risk" for a defined-risk structure, "cash deployed" for a shares book) — one label, not a
 * second formula. */
export function ReturnOnRiskCard({
  ror,
  capitalLabel = "capital at risk",
  rowWord = "settled rows",
}: {
  ror: { capitalAtRisk: number | null; returnOnRiskPct: number | null; returnOnRiskRawPct: number | null; coverage: string } | null;
  capitalLabel?: string;
  rowWord?: string;
}) {
  // A payload from an OLDER server (a field the reader had not grown yet) degrades to "nothing"
  // rather than crashing the page while the build and the process disagree.
  if (!ror || ror.returnOnRiskRawPct === null) return <TsEmpty what="return on risk" />;
  const raw = ror.returnOnRiskRawPct;
  const value = ror.returnOnRiskPct ?? 0;
  return (
    <TsCard title="return on risk">
      <GaugeSvg
        g={ROR_DOMAIN}
        value={value}
        ticks={["−100", "+100"]}
        aria={`return on risk ${raw.toFixed(1)}%`}
      />
      <div className={`ts-gauge-value ${raw >= 0 ? "ts-pos" : "ts-neg"}`}>{raw.toFixed(1)}%</div>
      <p className="ts-muted">
        of {ror.capitalAtRisk === null ? "—" : fmtUsd(ror.capitalAtRisk).replace("+$", "$")} {capitalLabel} ·{" "}
        {ror.coverage ?? "n/r"} {rowWord}
      </p>
    </TsCard>
  );
}

/* ---------------------------------------------------------------------------
 * Win rate — outer ring: sessions whose net came out positive (counted over the window's daily
 * rows, the page's own presentation arithmetic); inner ring: settled trades won. Both fractions
 * are counts, no money.
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

export function WinRateCard({
  trades,
  wins,
  daily,
}: {
  trades: number;
  wins: number;
  daily: ReadonlyArray<{ session: string; net: number; trades: number; wins: number }>;
}) {
  const daysGreen = daily.filter((d) => d.net > 0).length;
  if (trades === 0 && daily.length === 0) return <TsEmpty what="win rate" />;
  const dayPct = daily.length > 0 ? (daysGreen / daily.length) * 100 : 0;
  const tradePct = trades > 0 ? (wins / trades) * 100 : 0;
  return (
    <TsCard title="win rate">
      <div className="ts-rings">
        <svg viewBox="0 0 140 140" role="img" aria-label={`sessions ${dayPct.toFixed(1)}%, trades ${tradePct.toFixed(1)}%`}>
          <Ring r={54} fraction={dayPct / 100} className="ts-ring-days" />
          <Ring r={36} fraction={tradePct / 100} className="ts-ring-entries" />
        </svg>
        <div className="ts-rings-center">
          <span className={dayPct >= 50 ? "ts-pos" : "ts-neg"}>{dayPct.toFixed(0)}%</span>
          <span className="ts-muted">sessions</span>
        </div>
      </div>
      <dl className="ts-legend">
        <div><span className="ts-swatch ts-ring-days" /> sessions net positive {daysGreen}/{daily.length}</div>
        <div><span className="ts-swatch ts-ring-entries" /> trades {wins}/{trades}</div>
      </dl>
    </TsCard>
  );
}

/* The toolbar's range segments — the same five windows every module's summary names. */
export function RangeSegments({ range, onRange }: { range: Range; onRange: (r: Range) => void }) {
  return (
    <div className="ts-seg" role="tablist" aria-label="range">
      {RANGES.map((r) => (
        <button key={r} role="tab" aria-selected={range === r} className={range === r ? "ts-seg-on" : ""} onClick={() => onRange(r)}>
          {r}
        </button>
      ))}
    </div>
  );
}
