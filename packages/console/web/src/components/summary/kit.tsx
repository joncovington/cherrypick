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
 * A card's chosen face, remembered per card across reloads. Browser storage is a per-viewer
 * convenience here: unreadable or unwritable (a private window, a capture) falls back to the
 * default face, and an unknown stored value is ignored. The default is always `faces[0]`.
 */
export function useFace<F extends string>(key: string, faces: readonly F[]): [F, () => void, (f: F) => void] {
  const storageKey = `ts-face:${key}`;
  const [face, setFaceState] = useState<F>(() => {
    try {
      const stored = window.localStorage.getItem(storageKey);
      if (stored !== null && (faces as readonly string[]).includes(stored)) return stored as F;
    } catch {
      // storage unavailable: the default face
    }
    return faces[0]!;
  });
  const setFace = (f: F) => {
    setFaceState(f);
    try {
      window.localStorage.setItem(storageKey, f);
    } catch {
      // storage unavailable: the choice lasts this page only
    }
  };
  // A face that left the list (payoff with no losses) reads as the default rather than a blank.
  const current = (faces as readonly string[]).includes(face) ? face : faces[0]!;
  const next = () => setFace(faces[(faces.indexOf(current) + 1) % faces.length]!);
  return [current, next, setFace];
}

/** A small segmented switch for a card's aside: the faces as words, the current one marked. */
export function FaceSwitch<F extends string>({ faces, face, onFace, labels }: { faces: readonly F[]; face: F; onFace: (f: F) => void; labels?: Partial<Record<F, string>> }) {
  return (
    <span className="ts-faces" role="group" aria-label="show">
      {faces.map((f) => (
        <button key={f} type="button" className={f === face ? "ts-face-on" : ""} aria-pressed={f === face} onClick={() => onFace(f)}>
          {labels?.[f] ?? f}
        </button>
      ))}
    </span>
  );
}

/** A gauge's dollar face (SummaryGauge.dollars): the numerator and base its percentage divides. */
export interface GaugeDollars {
  amount: number;
  of: number | null;
  ofLabel: string;
  worst?: number | null;
}

/**
 * A half-circle gauge over [min, max]: arc from zero to the value, needle at the value. The
 * needle clamps to the domain; the label shows the UNclamped figure, so a big day pins the rail
 * without misreporting itself. With `dollars`, a click flips the figure between the percentage and
 * the dollars behind it ("$91.7k of $95.5k premium sold"); the needle stays, so the card keeps its
 * shape. The percentage is the default face.
 */
export function Gauge({ title, rawPct, min, max, caption, dollars }: { title: string; rawPct: number | null; min: number; max: number; caption: ReactNode; dollars?: GaugeDollars | null }) {
  const [face, next] = useFace(`gauge:${title}`, ["pct", "usd"] as const);
  if (rawPct === null) return <TsEmpty what={title} />;
  const theta = (v: number) => ((Math.min(Math.max(v, min), max) - min) / (max - min)) * 180;
  const zero = theta(Math.min(Math.max(0, min), max));
  const at = theta(rawPct);
  const [from, to] = at >= zero ? [zero, at] : [at, zero];
  const [nx, ny] = gpolar(G.r - 18, at);
  const tick = (v: number) => (v > 0 ? `+${v}` : v < 0 ? `−${Math.abs(v)}` : "0");
  const showUsd = face === "usd" && dollars != null;
  const body = (
    <>
      <svg className="ts-gauge" viewBox="0 0 180 104" role="img" aria-label={`${title} ${rawPct.toFixed(1)}%`}>
        <path d={gaugeArc(0, 180)} className="ts-track" strokeWidth={G.stroke} fill="none" strokeLinecap="round" />
        <path d={gaugeArc(from, to)} className={rawPct >= 0 ? "ts-arc-pos" : "ts-arc-neg"} strokeWidth={G.stroke} fill="none" strokeLinecap="round" />
        <line x1={G.cx} y1={G.cy} x2={nx} y2={ny} className="ts-needle" strokeWidth={2} />
        <circle cx={G.cx} cy={G.cy} r={3.5} className="ts-needle" />
        <text x={8} y={100} className="ts-tick">{tick(min)}</text>
        <text x={max >= 100 ? 160 : 168} y={100} className="ts-tick">{tick(max)}</text>
      </svg>
      {showUsd ? (
        <div className={`ts-gauge-value ${dollars.amount >= 0 ? "ts-pos" : "ts-neg"}`}>{fmtUsd(dollars.amount)}</div>
      ) : (
        <div className={`ts-gauge-value ${rawPct >= 0 ? "ts-pos" : "ts-neg"}`}>{rawPct.toFixed(1)}%</div>
      )}
    </>
  );
  return (
    <TsCard title={title}>
      {dollars == null ? (
        body
      ) : (
        <button type="button" className="ts-flip" onClick={next} aria-pressed={showUsd} title={`showing ${showUsd ? "dollars" : "percent"} — click for ${showUsd ? "percent" : "dollars"}`}>
          {body}
        </button>
      )}
      {showUsd ? (
        <p className="ts-muted">
          of {dollars.of === null ? "n/r" : fmtUsdPlain(dollars.of)} {dollars.ofLabel}
          {dollars.worst != null && (
            <>
              {" · "}worst trade <span className={dollars.worst >= 0 ? "ts-pos" : "ts-neg"}>{fmtUsd(dollars.worst)}</span>
            </>
          )}
        </p>
      ) : (
        <p className="ts-muted">{caption}</p>
      )}
    </TsCard>
  );
}

/** The window's per-trade shape (SummaryShape), as the cards read it. The uncertainty fields are
 *  optional: a page served by an older server has none, and shows no interval rather than a wrong one. */
export interface TradeShape {
  median: number | null;
  avgWin: number | null;
  avgLoss: number | null;
  worst: number | null;
  n?: number;
  mean?: number | null;
  sd?: number | null;
  ci95?: [number, number] | null;
  t?: number | null;
  winRateCi95?: [number, number] | null;
  skewness?: number | null;
  skew?: "left" | "right" | null;
  meanR?: number | null;
  rN?: number;
  rCi95?: [number, number] | null;
  /** B2's overlap flag: sessions that closed more than one trade, and the most in one. Trades that
   *  share a session share a tape, so the interval (which treats them as independent) reads narrower
   *  than it is. Counted by closing session only: a multi-day overlap is not seen here. */
  overlap?: { sessions: number; of: number; maxPerSession: number; heldTogether?: number | null } | null;
}

/** "0.12R (95% -0.05R to 0.29R) over 40 of 42", or null with no R (B3). */
export function rLine(shape: TradeShape | null | undefined, trades: number): string | null {
  if (shape?.meanR == null) return null;
  const r = (v: number) => `${v.toFixed(2)}R`;
  const ci = shape.rCi95 ? ` (95% ${r(shape.rCi95[0])} to ${r(shape.rCi95[1])})` : "";
  const of = shape.rN != null && shape.rN < trades ? ` over ${shape.rN} of ${trades}` : "";
  return `${r(shape.meanR)} per trade${ci}${of}`;
}

/** B2: the overlap note, or null when no session closed more than one trade. */
export function overlapNote(shape: TradeShape | null | undefined): string | null {
  const o = shape?.overlap;
  if (!o) return null;
  const parts: string[] = [];
  if (o.sessions > 0) parts.push(`${o.sessions} of ${o.of} sessions closed several trades (up to ${o.maxPerSession})`);
  if (o.heldTogether) parts.push(`${o.heldTogether} trades were held alongside another in their arm`);
  if (parts.length === 0) return null;
  return `${parts.join("; ")}: the interval treats them as independent, so it reads narrower than it is`;
}

/** Under this many trades a figure is an early estimate, whatever its interval says. */
export const EARLY_TRADES = 30;

/** "95% −$12.40 to $31.10 · t 1.2", or null when the shape carries no interval. */
export function intervalLine(shape?: TradeShape | null): string | null {
  if (!shape?.ci95) return null;
  const [lo, hi] = shape.ci95;
  return `95% ${fmtUsd(lo)} to ${fmtUsd(hi)}${shape.t == null ? "" : ` · t ${shape.t.toFixed(1)}`}`;
}

/** What the shape's skew says about where the result comes from, or null when it says nothing. */
export function skewNote(shape?: TradeShape | null): string | null {
  if (shape?.skew === "left") return "rare large losses carry the risk: judge the worst trade, not the median";
  if (shape?.skew === "right") return "a few large wins carry the result";
  return null;
}

/**
 * The net card's hero figure: the window's total by default, a click for net per trade with the
 * median and the average win and loss beneath it. A total says how much; per trade says how it was
 * made -- 555 small wins and one large one read alike as a total. The per-trade face also says how
 * sure it is: its 95% interval and t, an early-estimate warning under 30 trades, and what a strong
 * skew means (B2).
 */
export function NetHero({ net, trades, shape }: { net: number; trades: number; shape?: TradeShape | null }) {
  const [face, next] = useFace("net-hero", ["total", "per-trade"] as const);
  const perTrade = face === "per-trade" && trades > 0;
  const shown = perTrade ? net / trades : net;
  const fmt = (v: number | null | undefined) => (v == null ? "n/r" : fmtUsd(v));
  const interval = intervalLine(shape);
  const skew = skewNote(shape);
  const rl = rLine(shape, trades);
  const ov = overlapNote(shape);
  return (
    <button type="button" className="ts-flip ts-flip-left" onClick={next} aria-pressed={perTrade} title={`showing ${perTrade ? "net per trade" : "total net"} — click for ${perTrade ? "the total" : "net per trade"}`}>
      <span className={`ts-hero ${shown >= 0 ? "ts-pos" : "ts-neg"}`}>{fmtUsd(shown)}</span>
      <span className="ts-muted ts-hero-sub">
        {perTrade
          ? `per trade over ${trades} · median ${fmt(shape?.median)} · avg win ${fmt(shape?.avgWin)} · avg loss ${fmt(shape?.avgLoss)} · worst ${fmt(shape?.worst)}`
          : `total over ${trades} trade${trades === 1 ? "" : "s"} · click for per trade`}
      </span>
      {perTrade && (interval || rl || trades < EARLY_TRADES || skew || ov) && (
        <span className="ts-muted ts-hero-sub">
          {[interval, rl, trades < EARLY_TRADES ? `under ${EARLY_TRADES} trades: an early estimate` : null, skew, ov]
            .filter(Boolean)
            .join(" · ")}
        </span>
      )}
    </button>
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
 * centre reads one figure and a click steps it: trades (the default -- the per-trade rate is the
 * one every other surface quotes, and a session's sign can hide a losing trade behind a winning
 * one), then sessions, then the payoff ratio -- average win ÷ average loss, the number that makes
 * a win rate honest (80% wins at 1:6 loses money). The payoff face is offered only when the window
 * has both a win and a loss; it carries no tone, since a ratio has no sign.
 *
 * A win rate is never shown alone (B2; Goodhart: a premium seller can win 85% of its trades and lose
 * money). The legend always carries net per trade with its interval beside the rate, and the rate's
 * own interval. The centre takes its tone from net per trade, not from the rate passing 50%; with no
 * net per trade it carries none.
 */
export function WinRateArcs({
  daysWon,
  days,
  wins,
  trades,
  tradeNoun = "trades",
  avgWin = null,
  avgLoss = null,
  shape = null,
}: {
  daysWon: number;
  days: number;
  wins: number;
  trades: number;
  tradeNoun?: string;
  avgWin?: number | null;
  avgLoss?: number | null;
  shape?: TradeShape | null;
}) {
  const payoff = avgWin !== null && avgLoss !== null && avgLoss < 0 ? avgWin / Math.abs(avgLoss) : null;
  const faces = payoff === null ? (["trades", "sessions"] as const) : (["trades", "sessions", "payoff"] as const);
  const [face, next] = useFace<"trades" | "sessions" | "payoff">("win-rate", faces);
  if (trades === 0 && days === 0) return <TsEmpty what="win rate" />;
  const dayPct = days > 0 ? (daysWon / days) * 100 : 0;
  const tradePct = trades > 0 ? (wins / trades) * 100 : 0;
  const nextFace = faces[(faces.indexOf(face as never) + 1) % faces.length]!;
  const name = (f: string) => (f === "trades" ? tradeNoun : f === "sessions" ? "sessions" : "payoff");
  const mean = shape?.mean ?? null;
  const tone = mean === null ? "ts-plain" : mean >= 0 ? "ts-pos" : "ts-neg";
  const interval = intervalLine(shape);
  const rateCi = shape?.winRateCi95 ?? null;
  return (
    <TsCard title="win rate">
      <button type="button" className="ts-rings" onClick={next} title={`showing ${name(face)} — click for ${name(nextFace)}`}>
        <svg viewBox="0 0 140 140" role="img" aria-label={`sessions ${dayPct.toFixed(1)}%, ${tradeNoun} ${tradePct.toFixed(1)}%`}>
          <Ring r={54} fraction={dayPct / 100} className="ts-ring-days" />
          <Ring r={36} fraction={tradePct / 100} className="ts-ring-entries" />
        </svg>
        <span className="ts-rings-center">
          {face === "payoff" && payoff !== null ? (
            <>
              <span className="ts-plain">{payoff.toFixed(2)}×</span>
              <span className="ts-muted">win ÷ loss</span>
            </>
          ) : (
            <>
              <span className={tone} title="coloured by net per trade, not by the rate">
                {(face === "sessions" ? dayPct : tradePct).toFixed(0)}%
              </span>
              <span className="ts-muted">{name(face)}</span>
            </>
          )}
        </span>
      </button>
      <dl className="ts-legend">
        <div>
          <span className="ts-swatch ts-ring-days" /> sessions {daysWon}/{days}
        </div>
        <div>
          <span className="ts-swatch ts-ring-entries" /> {tradeNoun} {wins}/{trades}
          {rateCi && (
            <span className="ts-muted">
              {" "}
              (95% {(rateCi[0] * 100).toFixed(0)}–{(rateCi[1] * 100).toFixed(0)}%)
            </span>
          )}
        </div>
        <div className={mean === null ? "ts-muted" : mean >= 0 ? "ts-pos" : "ts-neg"} title="A win rate says nothing without what each trade nets: shown beside it, always">
          net per trade {mean === null ? "n/r" : fmtUsd(mean)}
          {interval && <span className="ts-muted"> · {interval}</span>}
        </div>
        {face === "payoff" && avgWin !== null && avgLoss !== null && (
          <div className="ts-muted">
            avg win {fmtUsd(avgWin)} · avg loss {fmtUsd(avgLoss)}
          </div>
        )}
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
