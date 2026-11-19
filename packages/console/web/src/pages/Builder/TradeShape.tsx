import { useEffect, useId, useState, type ReactNode } from "react";
import type { BuilderPayoff, BuilderSketch } from "@console/shared";
import { builderPayoff } from "../../lib/api";
import { fmtCash } from "../../lib/format";
import { isLegId, openAbbr, openVerb, titleCase } from "./legWords";

/**
 * The trade's shape beside a chain example, both from the module (`cli builder-payoff`).
 *
 * The **shape** is generic, drawn from the rule alone (`sketch`): each leg named where it sits, at its
 * distance from the spot (spot is 0) or at its delta, the y axis in words (max profit, 0, max loss),
 * never dollars. It shows whether or not a session can place the legs.
 *
 * The **chain** is an example: the legs as the engine placed them on live quotes, or on the last
 * session our own recorder holds, each row named by its distance from the strike nearest the spot,
 * each leg marked on its own side with its contracts, and that example's credit and extremes in a
 * line above it. The page draws what the module returned and works out nothing.
 *
 * A structure with a far leg (a calendar, a diagonal) is drawn at the near expiry with the far leg's
 * time value by a model (`modeled`): its peak is the model's, so the line and the caption say so.
 */

/** The keys the module never reads from an edit: the Builder's own `editableSpec` drops them too. */
const HIDDEN = new Set(["name", "register", "enabled"]);
/** How long an edit rests before the module is asked again. */
const SETTLE_MS = 700;

export function sendable(spec: Record<string, unknown>): Record<string, unknown> {
  return Object.fromEntries(Object.entries(spec).filter(([k]) => !HIDDEN.has(k)));
}

/** Ask the module for the structure: by name for a saved automation, with the edit for an unsaved one. */
export function usePayoff(name: string, automation: Record<string, unknown> | undefined) {
  const key = automation === undefined ? null : JSON.stringify(sendable(automation));
  const [state, setState] = useState<{ key: string; data: BuilderPayoff | null }>({ key: "", data: null });
  const want = `${name}|${key ?? "saved"}`;
  const legs = automation === undefined ? null : (automation["legs"] as unknown[] | undefined);
  const empty = Array.isArray(legs) && legs.length === 0;
  useEffect(() => {
    if (empty || !name) return;
    let live = true;
    const t = setTimeout(
      () => {
        void builderPayoff(name, key === null ? undefined : (JSON.parse(key) as Record<string, unknown>)).then((r) => {
          if (live) setState({ key: want, data: r.error ? { name, symbol: "", state: "not_placed", error: r.error } : r });
        });
      },
      key === null ? 0 : SETTLE_MS,
    );
    return () => {
      live = false;
      clearTimeout(t);
    };
  }, [want, key, name, empty]);
  return { empty, data: state.key === want ? state.data : null, stale: state.key !== want && state.data !== null, last: state.data };
}

/** "0 · spot", "+5", "−10": a chain row by its distance from the strike nearest the spot. */
export function offsetWords(offset: number): string {
  const d = Math.round(offset * 100) / 100;
  if (Math.abs(d) < 0.005) return "0 · spot";
  return `${d > 0 ? "+" : "−"}${Math.abs(d)}`;
}

/** A leg's name in words: "L2 · Short Put" for an ID, its own name title-cased for an older one. */
export function legName(leg: { name: string; side: number; right: string }): string {
  const words = `${leg.side < 0 ? "Short" : "Long"} ${leg.right === "C" ? "Call" : "Put"}`;
  return isLegId(leg.name) ? `${leg.name} · ${words}` : titleCase(leg.name) || words;
}

/** "STO 2 · L2 · Short Put", "BTO · Long Call": an entry leg as an order chain writes it, with its
 *  contracts when more than one. `legOrderWords` is the same in full, for the tag's title. */
type OrderLeg = { name: string; side: number; right: string; expiry?: "far" };

export function legOrder(leg: OrderLeg): string {
  const n = Math.abs(leg.side);
  return `${openAbbr(leg.side)}${n > 1 ? ` ${n}` : ""} · ${legName(leg)}${leg.expiry === "far" ? " · far" : ""}`;
}

export function legOrderWords(leg: OrderLeg): string {
  const n = Math.abs(leg.side);
  return `${openVerb(leg.side)}${n > 1 ? ` ${n}` : ""} · ${legName(leg)}${leg.expiry === "far" ? " · on the far expiry" : ""}`;
}

/** The example's line: where it was placed and what it came to. */
export function exampleWords(p: BuilderPayoff): string {
  const s = p.source;
  if (!s) return "";
  const [day, time] = s.at.split("T");
  const where = s.kind === "live" ? `on live quotes, ${day} at ${time ?? "?"}` : `on the last recorded session, ${day} at ${time ?? "?"}`;
  const mid = p.prices?.["mid"];
  const bits: string[] = [];
  if (mid !== undefined) bits.push(mid >= 0 ? `credit ${mid.toFixed(2)} at the mid` : `debit ${(-mid).toFixed(2)} at the mid`);
  if (p.modeled) bits.push(`about ${fmtCash(p.modeled.peak)} at most at the near expiry (modeled: ${modeledWords(p.modeled)})`);
  else bits.push(p.max_profit === null || p.max_profit === undefined ? "profit unlimited" : `at most ${fmtCash(p.max_profit)}`);
  bits.push(p.max_loss === null || p.max_loss === undefined ? "loss unlimited" : `at worst ${fmtCash(-p.max_loss)} a contract`);
  return `For example, ${where}: ${bits.join(", ")}.`;
}

/** "L2 at 18.4% vol, 28 days left": what the model holds each far leg at. */
export function modeledWords(m: NonNullable<BuilderPayoff["modeled"]>): string {
  return m.legs
    .map((l) => `${l.name} ${l.iv === null ? "at its intrinsic value" : `at ${(l.iv * 100).toFixed(1)}% vol`}, ${l.days} days left`)
    .join("; ");
}

const W = 580;
const H = 250;
const PAD = { l: 92, r: 16, t: 26, b: 44 };

/** The chart's scales and paths; exported for its test. */
export function shapeGeometry(curve: Array<[number, number]>) {
  const xs = curve.map((p) => p[0]);
  const ys = curve.map((p) => p[1]);
  const x0 = Math.min(...xs);
  const x1 = Math.max(...xs);
  const top = Math.max(0, ...ys);
  const bottom = Math.min(0, ...ys);
  const room = (top - bottom || 1) * 0.06;
  const hi = top + room;
  const lo = bottom - room;
  const x = (v: number) => PAD.l + ((v - x0) / (x1 - x0 || 1)) * (W - PAD.l - PAD.r);
  const y = (v: number) => PAD.t + ((hi - v) / (hi - lo || 1)) * (H - PAD.t - PAD.b);
  const line = curve.map(([a, b], i) => `${i === 0 ? "M" : "L"}${x(a).toFixed(1)},${y(b).toFixed(1)}`).join(" ");
  const area = `${line} L${x(x1).toFixed(1)},${y(0).toFixed(1)} L${x(x0).toFixed(1)},${y(0).toFixed(1)} Z`;
  return { x, y, x0, x1, top, bottom, line, area };
}

/** The curve's value at `at`: straight between its points. */
export function valueAt(curve: Array<[number, number]>, at: number): number {
  for (let i = 1; i < curve.length; i++) {
    const [a, ya] = curve[i - 1]!;
    const [b, yb] = curve[i]!;
    if (at >= a && at <= b) return b === a ? ya : ya + ((yb - ya) * (at - a)) / (b - a);
  }
  return curve.length ? curve[curve.length - 1]![1] : 0;
}

/** The row each tick's label sits on: the first row, or the second where it would crowd the label
 *  before it (`gap` px apart), so close ticks stay readable. */
export function labelRows(xs: number[], gap = 40): number[] {
  const last = [-Infinity, -Infinity];
  return xs.map((x) => {
    const row = x - last[0]! >= gap ? 0 : 1;
    last[row] = x;
    return row;
  });
}

/** The legs at one position as one marker: "Short Put · Short Call", "L2 · Short Put ×2". */
export function markers(s: BuilderSketch): Array<{ at: number; text: string; sold: boolean; many: boolean }> {
  const byAt = new Map<number, BuilderSketch["legs"]>();
  for (const g of s.legs) byAt.set(g.at, [...(byAt.get(g.at) ?? []), g]);
  return [...byAt.entries()]
    .sort((a, b) => a[0] - b[0])
    .map(([at, legs]) => ({
      at,
      text: legs
        .map((g) => {
          const n = Math.abs(g.side);
          const words = `${g.side < 0 ? "Short" : "Long"} ${g.right === "C" ? "Call" : "Put"}`;
          if (!isLegId(g.name)) return titleCase(g.name) || words;
          return `${g.name} · ${words}${n > 1 ? ` ×${n}` : ""}${g.expiry === "far" ? " · far" : ""}`;
        })
        .join(" · "),
      sold: legs.some((g) => g.side < 0),
      many: legs.some((g) => Math.abs(g.side) > 1),
    }));
}

function Shape({ s }: { s: BuilderSketch }) {
  const g = shapeGeometry(s.curve);
  const zero = g.y(0);
  const id = useId().replace(/:/g, "");
  const rows = labelRows(s.ticks.map((t) => g.x(t.at)));
  return (
    <figure className="pay-figure tshape-figure">
      <svg viewBox={`0 0 ${W} ${H}`} className="pay-svg" role="img" aria-label="The structure's shape at expiry, each leg named where it sits">
        <defs>
          <clipPath id={`${id}-up`}>
            <rect x={0} y={0} width={W} height={zero} />
          </clipPath>
          <clipPath id={`${id}-down`}>
            <rect x={0} y={zero} width={W} height={H - zero} />
          </clipPath>
        </defs>
        <path d={g.area} className="tshape-gain" clipPath={`url(#${id}-up)`} />
        <path d={g.area} className="tshape-loss" clipPath={`url(#${id}-down)`} />
        <line x1={PAD.l} x2={W - PAD.r} y1={zero} y2={zero} className="pay-zero" />
        <line x1={g.x(0)} x2={g.x(0)} y1={PAD.t} y2={H - PAD.b} className="pay-spot" />
        <path d={g.line} className="pay-line tshape-line" />
        {g.top > 0 && (
          <text x={PAD.l - 8} y={g.y(g.top) + 4} textAnchor="end" className="tshape-word">
            {s.top ?? "max profit"}
          </text>
        )}
        <text x={PAD.l - 8} y={zero + 4} textAnchor="end" className="pay-axis">
          0
        </text>
        {g.bottom < 0 && (
          <text x={PAD.l - 8} y={g.y(g.bottom) + 4} textAnchor="end" className="tshape-word">
            {s.bottom ?? "max loss"}
          </text>
        )}
        {s.ticks.map((t, i) => (
          <text key={`t${t.at}`} x={g.x(t.at)} y={H - PAD.b + 18 + rows[i]! * 13} textAnchor="middle" className={t.at === 0 ? "pay-axis tshape-spot" : "pay-axis"}>
            {t.label}
          </text>
        ))}
        {markers(s).map((m) => {
          const x = g.x(m.at);
          const y = g.y(valueAt(s.curve, m.at));
          const anchor = x < PAD.l + 70 ? "start" : x > W - PAD.r - 70 ? "end" : "middle";
          const above = y > PAD.t + 16;
          return (
            <g key={`m${m.at}`}>
              <circle cx={x} cy={y} r={m.many ? 6 : 4.5} className={m.sold ? "tshape-sold" : "tshape-bought"} />
              <text x={x} y={above ? y - 10 : y + 18} textAnchor={anchor} className={m.sold ? "tshape-leg is-sold" : "tshape-leg is-bought"}>
                {m.text}
              </text>
            </g>
          );
        })}
      </svg>
      <figcaption className="muted tshape-note">
        {s.exact
          ? "Drawn from the rule: each leg at its distance from the spot. Not to scale in dollars."
          : "Drawn from the rule: a leg placed by delta or a multiple sits where it reads, so the order is true and the spacing is not. Not to scale in dollars."}
        {s.modeled && " At the near expiry, with the far leg's time value by a model: the peak is the model's, never a bound."}
      </figcaption>
    </figure>
  );
}

function quote(v: number | null | undefined): string {
  return v === null || v === undefined ? "—" : v.toFixed(2);
}

function Chain({ p }: { p: BuilderPayoff }) {
  const rows = p.chain ?? [];
  const on = (k: number, right: string) => (p.legs ?? []).filter((l) => l.strike === k && l.right === right);
  const cls = (k: number, right: string) => {
    const ls = on(k, right);
    return ls.length === 0 ? "" : ls.some((l) => l.side < 0) ? "tchain-sell" : "tchain-buy";
  };
  const tags = (k: number, right: string) =>
    on(k, right).map((l) => (
      <span key={l.name} className={l.side < 0 ? "tchain-tag is-sold" : "tchain-tag is-bought"} title={legOrderWords(l)}>
        {legOrder(l)}
      </span>
    ));
  return (
    <div className="table-scroll tchain">
      <table className="data-table tchain-table">
        <thead>
          <tr>
            <th className="num">call Δ</th>
            <th className="num">call leg</th>
            <th className="tchain-k">strike</th>
            <th>put leg</th>
            <th className="num">put Δ</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <Rows key={r.strike} gap={r.gap}>
              <tr className={r.offset === 0 ? "tchain-atm" : undefined}>
                <td className={`num ${cls(r.strike, "C")}`}>{quote(r.call?.delta)}</td>
                <td className={`num ${cls(r.strike, "C")}`}>{tags(r.strike, "C")}</td>
                <td className="tchain-k">{offsetWords(r.offset)}</td>
                <td className={cls(r.strike, "P")}>{tags(r.strike, "P")}</td>
                <td className={`num ${cls(r.strike, "P")}`}>{quote(r.put?.delta)}</td>
              </tr>
            </Rows>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Rows({ gap, children }: { gap: boolean; children: ReactNode }) {
  return (
    <>
      {gap && (
        <tr className="tchain-gap">
          <td colSpan={5}>⋯</td>
        </tr>
      )}
      {children}
    </>
  );
}

/** Why there is no example, in the module's own words. */
function NoExample({ p }: { p: BuilderPayoff }) {
  if (p.error) return <p className="tchain-none">The example could not be read: {p.error}</p>;
  // One reason for both sources (a far leg, which neither can draw) is said once.
  if (p.live && p.live === p.recorded) return <p className="tchain-none">No example yet: {p.live}.</p>;
  return (
    <p className="tchain-none">
      No example yet. On live quotes: {p.live ?? "—"}. On the last recorded session: {p.recorded ?? "—"}.
    </p>
  );
}

/** The generic shape beside the chain example. */
export function TradeShapeView({ p, stale = false }: { p: BuilderPayoff; stale?: boolean }) {
  if (p.state === "invalid") return <p className="muted">The legs are not complete yet: {(p.problems ?? []).slice(0, 2).join("; ")}</p>;
  if (!p.sketch) return <NoExample p={p} />;
  const placed = p.state === "placed" && !!p.chain;
  return (
    <div className={stale ? "tshape is-stale" : "tshape"} aria-busy={stale}>
      <div className="tshape-pair">
        <div>
          <h5 className="builder-lbl">{p.sketch.modeled ? "the shape · at the near expiry, modeled" : "the shape · one structure at expiry"}</h5>
          <Shape s={p.sketch} />
        </div>
        <div>
          <h5 className="builder-lbl">on the chain · where each leg lands</h5>
          {placed ? (
            <>
              <p className="tshape-example">{exampleWords(p)}</p>
              <Chain p={p} />
            </>
          ) : (
            <NoExample p={p} />
          )}
        </div>
      </div>
      <p className="muted tshape-note">An example of where the rules land, not a trade. Its money is at the mid, before fees and slippage.</p>
    </div>
  );
}

/** The module's structure for one automation: saved (by name) or an unsaved edit. */
export function TradeShape({ name, automation }: { name: string; automation?: Record<string, unknown> }) {
  const { empty, data, stale, last } = usePayoff(name, automation);
  if (empty) return <p className="muted">Add legs to see the trade's shape and where it lands on the chain.</p>;
  const shown = data ?? last;
  if (!shown) return <p className="muted">Placing the legs…</p>;
  return <TradeShapeView p={shown} stale={stale} />;
}
