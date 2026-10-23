import { useState, type ReactNode } from "react";
import { useSearchParams } from "react-router-dom";

/**
 * The summary slides' shared visual kit, lifted from MEIC's summary (the pattern every module's
 * simplified view follows): cards, the empty card, the half-circle gauge, the win-rate rings, the
 * range toolbar. Fixed-size hand-rolled SVG; colour carries a number's sign only, never a verdict.
 * Styles are the `.ts-*` block in styles.css.
 */

export const RANGES = ["day", "week", "month", "year", "all"] as const;
export type Range = (typeof RANGES)[number];

/** `+$1,580.00`: signed, grouped, whole-position dollars. */
export function fmtUsd(v: number): string {
  const sign = v < 0 ? "-" : "+";
  return `${sign}$${Math.abs(v).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

/** An unsigned dollar figure (a cost, a capital base): `$1,580.00`. */
export function fmtUsdPlain(v: number): string {
  return `$${Math.abs(v).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

/** "Wednesday, October 7, 2026" for an ISO session. */
export function sessionLabel(iso: string): string {
  const d = new Date(`${iso}T12:00:00Z`);
  return d.toLocaleDateString("en-US", { weekday: "long", year: "numeric", month: "long", day: "numeric", timeZone: "UTC" });
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

/** A card whose payload has nothing for it. A zero would read as "traded and broke even". */
export function TsEmpty({ what, why = "no rows in this window" }: { what: string; why?: string }) {
  return (
    <section className="ts-card">
      <header className="ts-card-head">
        <h3>{what}</h3>
      </header>
      <p className="ts-empty">{why}</p>
    </section>
  );
}

/* The gauge's geometry: angles 0° (left) to 180° (right), the domain's zero wherever it falls. */
const G = { cx: 90, cy: 84, r: 70, stroke: 13 };

function gpolar(r: number, theta: number): [number, number] {
  const rad = (theta * Math.PI) / 180;
  return [G.cx - r * Math.cos(rad), G.cy - r * Math.sin(rad)];
}

function gaugeArc(a: number, b: number): string {
  const [x1, y1] = gpolar(G.r, a);
  const [x2, y2] = gpolar(G.r, b);
  return `M ${x1.toFixed(2)} ${y1.toFixed(2)} A ${G.r} ${G.r} 0 0 1 ${x2.toFixed(2)} ${y2.toFixed(2)}`;
}

/**
 * A half-circle gauge over [min, max]: arc from zero to the value, needle at the value. The
 * needle clamps to the domain; the label shows the UNclamped figure, so a big day pins the rail
 * without misreporting itself.
 */
export function Gauge({ title, rawPct, min, max, caption }: { title: string; rawPct: number | null; min: number; max: number; caption: ReactNode }) {
  if (rawPct === null) return <TsEmpty what={title} />;
  const theta = (v: number) => ((Math.min(Math.max(v, min), max) - min) / (max - min)) * 180;
  const zero = theta(Math.min(Math.max(0, min), max));
  const at = theta(rawPct);
  const [from, to] = at >= zero ? [zero, at] : [at, zero];
  const [nx, ny] = gpolar(G.r - 18, at);
  const tick = (v: number) => (v > 0 ? `+${v}` : v < 0 ? `−${Math.abs(v)}` : "0");
  return (
    <TsCard title={title}>
      <svg className="ts-gauge" viewBox="0 0 180 104" role="img" aria-label={`${title} ${rawPct.toFixed(1)}%`}>
        <path d={gaugeArc(0, 180)} className="ts-track" strokeWidth={G.stroke} fill="none" strokeLinecap="round" />
        <path d={gaugeArc(from, to)} className={rawPct >= 0 ? "ts-arc-pos" : "ts-arc-neg"} strokeWidth={G.stroke} fill="none" strokeLinecap="round" />
        <line x1={G.cx} y1={G.cy} x2={nx} y2={ny} className="ts-needle" strokeWidth={2} />
        <circle cx={G.cx} cy={G.cy} r={3.5} className="ts-needle" />
        <text x={8} y={100} className="ts-tick">{tick(min)}</text>
        <text x={max >= 100 ? 160 : 168} y={100} className="ts-tick">{tick(max)}</text>
      </svg>
      <div className={`ts-gauge-value ${rawPct >= 0 ? "ts-pos" : "ts-neg"}`}>{rawPct.toFixed(1)}%</div>
      <p className="ts-muted">{caption}</p>
    </TsCard>
  );
}

function Ring({ r, fraction, className }: { r: number; fraction: number; className: string }) {
  const c = 2 * Math.PI * r;
  return (
    <>
      <circle cx={70} cy={70} r={r} className="ts-track" strokeWidth={11} fill="none" />
      <circle
        cx={70}
        cy={70}
        r={r}
        className={className}
        strokeWidth={11}
        fill="none"
        strokeLinecap="round"
        strokeDasharray={`${(fraction * c).toFixed(2)} ${c.toFixed(2)}`}
        transform="rotate(-90 70 70)"
      />
    </>
  );
}

/**
 * Two concentric rings: outer = sessions with positive net, inner = settled trades that won. The
 * centre reads one of them; a click flips it. Trades by default -- the per-trade rate is the one
 * every other surface quotes, and a session's sign can hide a losing trade behind a winning one.
 */
export function WinRateArcs({ daysWon, days, wins, trades, tradeNoun = "trades" }: { daysWon: number; days: number; wins: number; trades: number; tradeNoun?: string }) {
  const [showSessions, setShowSessions] = useState(false);
  if (trades === 0 && days === 0) return <TsEmpty what="win rate" />;
  const dayPct = days > 0 ? (daysWon / days) * 100 : 0;
  const tradePct = trades > 0 ? (wins / trades) * 100 : 0;
  const centrePct = showSessions ? dayPct : tradePct;
  return (
    <TsCard title="win rate">
      <button
        type="button"
        className="ts-rings"
        onClick={() => setShowSessions((s) => !s)}
        title={`showing ${showSessions ? "sessions" : tradeNoun} — click for ${showSessions ? tradeNoun : "sessions"}`}
        aria-pressed={showSessions}
      >
        <svg viewBox="0 0 140 140" role="img" aria-label={`sessions ${dayPct.toFixed(1)}%, ${tradeNoun} ${tradePct.toFixed(1)}%`}>
          <Ring r={54} fraction={dayPct / 100} className="ts-ring-days" />
          <Ring r={36} fraction={tradePct / 100} className="ts-ring-entries" />
        </svg>
        <span className="ts-rings-center">
          <span className={centrePct >= 50 ? "ts-pos" : "ts-neg"}>{centrePct.toFixed(0)}%</span>
          <span className="ts-muted">{showSessions ? "sessions" : tradeNoun}</span>
        </span>
      </button>
      <dl className="ts-legend">
        <div>
          <span className="ts-swatch ts-ring-days" /> sessions {daysWon}/{days}
        </div>
        <div>
          <span className="ts-swatch ts-ring-entries" /> {tradeNoun} {wins}/{trades}
        </div>
      </dl>
    </TsCard>
  );
}

/** The range rides the URL (?range=week); anything unstated or unknown is the day. */
export function useSummaryRange(): [Range, (r: Range) => void] {
  const [params, setParams] = useSearchParams();
  const stated = params.get("range");
  const range: Range = (RANGES as readonly string[]).includes(stated ?? "") ? (stated as Range) : "day";
  const setRange = (r: Range) => {
    const next = new URLSearchParams(params);
    if (r === "day") next.delete("range");
    else next.set("range", r);
    next.delete("page");
    setParams(next, { replace: true });
  };
  return [range, setRange];
}

/** Range segments, the window label and the day arrows. */
export function RangeToolbar({
  title = "trade summary",
  range,
  onRange,
  label,
  count,
  prevDisabled,
  nextDisabled,
  onDayStep,
}: {
  title?: string;
  range: Range;
  onRange: (r: Range) => void;
  label: string;
  count: ReactNode;
  prevDisabled: boolean;
  nextDisabled: boolean;
  onDayStep: (delta: -1 | 1) => void;
}) {
  return (
    <div className="ts-toolbar">
      <h2 className="ts-title">{title}</h2>
      <div className="ts-seg" role="tablist" aria-label="range">
        {RANGES.map((r) => (
          <button key={r} role="tab" aria-selected={range === r} className={range === r ? "ts-seg-on" : ""} onClick={() => onRange(r)}>
            {r}
          </button>
        ))}
      </div>
      <div className="ts-daynav">
        <button aria-label="previous session" disabled={prevDisabled} onClick={() => onDayStep(-1)}>
          ‹
        </button>
        <span>
          {label} <span className="ts-muted">· {count}</span>
        </span>
        <button aria-label="next session" disabled={nextDisabled} onClick={() => onDayStep(1)}>
          ›
        </button>
      </div>
    </div>
  );
}
