import { useState, type MouseEvent } from "react";
import type { LadderEquity, LadderEquityPoint } from "@console/shared";
import { Card } from "../../components/DataTable";
import { HelpSection, HelpWarn } from "../../components/CardHelp";
import { HoverReadout } from "../../components/chart/Tooltip";
import { fmtMoney } from "../../lib/format";

/**
 * Each arm by session, realized and marked: the module's `ladder equity` (`/api/ladder/equity`), drawn
 * as one small panel per arm on one shared scale. The figures are the module's; the page scales them
 * onto the panels and computes no money.
 *
 * Under a no-stop rule every sale is at a target, so the realized line only climbs while held lots
 * fall: the marked line (realized plus the open lots sold at the session's last bid, fees included) is
 * the one that says what the arm is worth, and the shaded gap between them is the open lots.
 */

const W = 360;
const H = 150;
const M = { l: 50, r: 70, t: 10, b: 20 };
const DOTS_UP_TO = 12; // markers on every session while a panel has this few

export interface Domain {
  lo: number;
  hi: number;
}

/** One scale for every panel: the arms are compared by eye, so they must share it. Zero is in it. */
export function sharedDomain(arms: Record<string, LadderEquityPoint[]>): Domain {
  const vals = [0];
  for (const pts of Object.values(arms)) {
    for (const p of pts) {
      vals.push(p.realised);
      if (p.marked !== null) vals.push(p.marked);
    }
  }
  const lo = Math.min(...vals);
  const hi = Math.max(...vals);
  const pad = (hi - lo || 1) * 0.08;
  return { lo: lo - pad, hi: hi + pad };
}

function niceStep(span: number): number {
  const raw = span / 3;
  const mag = 10 ** Math.floor(Math.log10(raw));
  const f = raw / mag;
  return (f < 1.5 ? 1 : f < 3.5 ? 2 : f < 7.5 ? 5 : 10) * mag;
}

export function ticks(d: Domain): number[] {
  const step = niceStep(d.hi - d.lo);
  const out: number[] = [];
  for (let v = Math.ceil(d.lo / step) * step; v <= d.hi; v += step) out.push(Math.round(v * 100) / 100);
  return out;
}

export interface Geometry {
  x: (i: number) => number;
  y: (v: number) => number;
  realised: string;
  /** The marked line, broken where a session has no mark. */
  marked: string;
  /** The gap, one polygon per run of one sign, split where the open lots cross zero. */
  under: string[];
  over: string[];
}

export function geometry(points: LadderEquityPoint[], d: Domain): Geometry {
  const n = points.length;
  const x = (i: number): number => (n === 1 ? (M.l + W - M.r) / 2 : M.l + (i * (W - M.l - M.r)) / (n - 1));
  const y = (v: number): number => M.t + (1 - (v - d.lo) / (d.hi - d.lo)) * (H - M.t - M.b);
  const f = (v: number): string => v.toFixed(1);
  const realised = points.map((p, i) => `${i === 0 ? "M" : "L"}${f(x(i))},${f(y(p.realised))}`).join(" ");
  let marked = "";
  points.forEach((p, i) => {
    if (p.marked === null) return;
    const joined = i > 0 && points[i - 1]!.marked !== null;
    marked += `${joined ? " L" : " M"}${f(x(i))},${f(y(p.marked))}`;
  });
  const under: string[] = [];
  const over: string[] = [];
  const quad = (x0: number, x1: number, r0: number, r1: number, m0: number, m1: number, below: boolean): void => {
    (below ? under : over).push(`M${f(x0)},${f(y(r0))} L${f(x1)},${f(y(r1))} L${f(x1)},${f(y(m1))} L${f(x0)},${f(y(m0))} Z`);
  };
  for (let i = 0; i + 1 < n; i++) {
    const a = points[i]!;
    const b = points[i + 1]!;
    if (a.marked === null || b.marked === null) continue;
    const ua = a.marked - a.realised;
    const ub = b.marked - b.realised;
    const x0 = x(i);
    const x1 = x(i + 1);
    if (ua * ub >= 0) {
      if (ua !== 0 || ub !== 0) quad(x0, x1, a.realised, b.realised, a.marked, b.marked, ua + ub < 0);
      continue;
    }
    const t = ua / (ua - ub); // where the open lots cross zero, along the segment
    const xc = x0 + t * (x1 - x0);
    const rc = a.realised + t * (b.realised - a.realised);
    quad(x0, xc, a.realised, rc, a.marked, rc, ua < 0);
    quad(xc, x1, rc, b.realised, rc, b.marked, ub < 0);
  }
  return { x, y, realised, marked: marked.trim(), under, over };
}

const fmtTick = (v: number): string => (v === 0 ? "$0" : `${v < 0 ? "−" : ""}$${Math.abs(v) >= 1000 ? `${(Math.abs(v) / 1000).toFixed(1)}k` : String(Math.round(Math.abs(v)))}`);

function readout(p: LadderEquityPoint): string[] {
  return [
    p.session,
    `marked     ${p.marked === null ? "no mark" : fmtMoney(p.marked)}`,
    `realized   ${fmtMoney(p.realised)}`,
    `open lots  ${p.unrealised === null ? "no mark" : fmtMoney(p.unrealised)}`,
    `${String(p.openLots)} open, cost ${fmtMoney(p.openCost)}${p.unmarked > 0 ? `, ${String(p.unmarked)} unmarked` : ""}`,
  ];
}

/** Where to hand `HoverReadout` its x: right of the crosshair on the left half, left of it on the
 *  right half, so the box never sits over the end labels. Its box is 5.8px a character plus 12. */
export function readoutAnchor(x: number, lines: string[]): number {
  if (x < (M.l + W - M.r) / 2) return x;
  const bw = Math.max(...lines.map((l) => l.length)) * 5.8 + 12;
  return x - bw - 24;
}

/** The two end labels, pushed apart so they never sit on one another. */
function endLabels(yMarked: number | null, yReal: number): { m: number | null; r: number } {
  if (yMarked === null) return { m: null, r: yReal };
  if (Math.abs(yMarked - yReal) >= 12) return { m: yMarked, r: yReal };
  const mid = (yMarked + yReal) / 2;
  return yMarked <= yReal ? { m: mid - 6, r: mid + 6 } : { m: mid + 6, r: mid - 6 };
}

export function EquityPanel({ arm, points, domain }: { arm: string; points: LadderEquityPoint[]; domain: Domain }) {
  const [hover, setHover] = useState<number | null>(null);
  const g = geometry(points, domain);
  const last = points[points.length - 1]!;
  const lastI = points.length - 1;
  const lab = endLabels(last.marked === null ? null : g.y(last.marked), g.y(last.realised));
  const move = (e: MouseEvent<SVGRectElement>): void => {
    const box = e.currentTarget.ownerSVGElement!.getBoundingClientRect();
    const sx = ((e.clientX - box.left) / box.width) * W;
    let best = 0;
    points.forEach((_, i) => {
      if (Math.abs(g.x(i) - sx) < Math.abs(g.x(best) - sx)) best = i;
    });
    setHover(best);
  };
  const dots = points.length <= DOTS_UP_TO;
  return (
    <figure className="ldr-eq-panel" data-arm={arm}>
      <figcaption className="ldr-eq-title">{arm}</figcaption>
      <svg viewBox={`0 0 ${String(W)} ${String(H)}`} role="img" aria-label={`${arm}: realized and marked net by session`} className="ldr-eq-svg">
        {ticks(domain).map((v) => (
          <g key={v}>
            <line x1={M.l} x2={W - M.r} y1={g.y(v)} y2={g.y(v)} className={v === 0 ? "ldr-eq-zero" : "ldr-eq-grid"} />
            <text x={M.l - 6} y={g.y(v) + 3} className="ldr-eq-tick" textAnchor="end">
              {fmtTick(v)}
            </text>
          </g>
        ))}
        <text x={M.l} y={H - 5} className="ldr-eq-tick">
          {points[0]!.session.slice(5)}
        </text>
        {points.length > 1 && (
          <text x={W - M.r} y={H - 5} className="ldr-eq-tick" textAnchor="end">
            {last.session.slice(5)}
          </text>
        )}
        {g.under.map((d, i) => (
          <path key={`u${String(i)}`} d={d} className="ldr-eq-under" />
        ))}
        {g.over.map((d, i) => (
          <path key={`o${String(i)}`} d={d} className="ldr-eq-over" />
        ))}
        {points.length === 1 && (
          // A lone session has no segment to shade or dash: the gap is a bar and realized a ring.
          <>
            {last.marked !== null && last.marked !== last.realised && (
              <rect
                x={g.x(0) - 4}
                width={8}
                y={Math.min(g.y(last.marked), g.y(last.realised))}
                height={Math.abs(g.y(last.marked) - g.y(last.realised))}
                rx={2}
                className={last.marked < last.realised ? "ldr-eq-under" : "ldr-eq-over"}
              />
            )}
            <circle cx={g.x(0)} cy={g.y(last.realised)} r={4} className="ldr-eq-dot-realised" />
          </>
        )}
        <path d={g.realised} className="ldr-eq-realised" fill="none" />
        {g.marked !== "" && <path d={g.marked} className="ldr-eq-marked" fill="none" />}
        {dots &&
          points.map((p, i) =>
            p.marked === null ? null : <circle key={p.session} cx={g.x(i)} cy={g.y(p.marked)} r={4} className="ldr-eq-dot" />,
          )}
        {lab.m !== null && (
          <text x={g.x(lastI) + 8} y={lab.m + 3} className="ldr-eq-end">
            marked {fmtTick(last.marked!)}
          </text>
        )}
        <text x={g.x(lastI) + 8} y={lab.r + 3} className="ldr-eq-end ldr-eq-end-muted">
          realized {fmtTick(last.realised)}
        </text>
        {hover !== null && (
          <>
            <line x1={g.x(hover)} x2={g.x(hover)} y1={M.t} y2={H - M.b} className="ldr-eq-cross" />
            <HoverReadout
              x={readoutAnchor(g.x(hover), readout(points[hover]!))}
              width={W}
              lines={readout(points[hover]!)}
              lineColor={(i) => (i === 0 ? "var(--text)" : "var(--text-muted)")}
            />
          </>
        )}
        <rect x={M.l} y={M.t} width={W - M.l - M.r} height={H - M.t - M.b} fill="transparent" onMouseMove={move} onMouseLeave={() => setHover(null)} />
      </svg>
    </figure>
  );
}

/** The config's arm order, then any arm the ledger holds that the config no longer declares. */
export function equityOrder(decl: Array<{ arm: string }> | undefined, equity: LadderEquity | undefined): string[] {
  const names = (decl ?? []).map((a) => a.arm);
  for (const a of Object.keys(equity?.arms ?? {})) if (!names.includes(a)) names.push(a);
  return names;
}

export function LadderEquityCard({ equity, order }: { equity: LadderEquity | undefined; order: string[] }) {
  const arms = equity?.ok ? order.filter((a) => (equity.arms[a]?.length ?? 0) > 0) : [];
  const domain = sharedDomain(Object.fromEntries(arms.map((a) => [a, equity!.arms[a]!])));
  const gaps = arms.reduce((n, a) => n + equity!.arms[a]!.filter((p) => p.marked === null).length, 0);
  return (
    <Card
      title="realized and marked, by session"
      collapseKey="ladder-equity"
      className="lv-12"
      isError={equity !== undefined && !equity.ok}
      help={{
        title: "Realized and marked",
        body: (
          <>
            <HelpWarn title="Read the solid line">
              <p>
                Every sale is at a target and nothing is ever stopped out, so the realized line can only climb while the lots still held fall. What an arm
                is worth is the marked line.
              </p>
            </HelpWarn>
            <HelpSection title="The lines">
              <p>
                Realized (dashed) is the net of the lots sold by each session&apos;s close. Marked (solid) adds the lots still open, each valued at what
                selling it at that session&apos;s last bid would net, both fills&apos; fees included. The shading between them is the open lots: red where
                they are under water, green where they are ahead. The latest session is marked at its latest tick, so it moves while the session runs.
              </p>
              <p>
                Each arm keeps its own book, so the panels are alternatives side by side, never added together. They share one scale. A session where an
                open lot&apos;s fund had no bid breaks the solid line: no price is carried forward.
              </p>
            </HelpSection>
            <HelpSection title="Paper only">
              <p>The live ledger keeps no marks, so the live book has no marked line.</p>
            </HelpSection>
          </>
        ),
      }}
    >
      {equity === undefined ? (
        <p className="muted">loading…</p>
      ) : !equity.ok ? (
        <p className="muted">{equity.error ?? "the ladder's read is unavailable"}</p>
      ) : arms.length === 0 ? (
        <p className="muted">no lot has been bought yet</p>
      ) : (
        <>
          <div className="ldr-eq-legend" aria-hidden="true">
            <span>
              <i className="ldr-eq-key ldr-eq-key-marked" /> marked: realized plus open lots at the bid
            </span>
            <span>
              <i className="ldr-eq-key ldr-eq-key-realised" /> realized
            </span>
            <span>
              <i className="ldr-eq-key ldr-eq-key-under" /> open lots under water
            </span>
            <span>
              <i className="ldr-eq-key ldr-eq-key-over" /> open lots ahead
            </span>
          </div>
          <div className="ldr-eq-grid-wrap">
            {arms.map((a) => (
              <EquityPanel key={a} arm={a} points={equity.arms[a]!} domain={domain} />
            ))}
          </div>
          {gaps > 0 && <p className="muted">{gaps === 1 ? "One session has" : `${String(gaps)} arm-sessions have`} no mark: the solid line breaks there.</p>}
        </>
      )}
    </Card>
  );
}
