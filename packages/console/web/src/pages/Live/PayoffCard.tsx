import { useState } from "react";
import type { ManagePayoff, ManagePayoffName } from "@console/shared";
import { fmtCash } from "../../lib/format";
import { CardLightbox, LightboxFrame, useOpenParam } from "../Backtest/CardLightbox";
import { useLiveMarks } from "./LiveMarks";

/**
 * Live → wheel's payoff (2026-11-04): manage's `payoff`, each held name's P&L across its price at the
 * nearest expiry and on dates before it. The curves, breakevens, vols and model are manage's; this
 * draws them, marks the evaluation's price and the stream's live price, and decides nothing. A name
 * opens its chart in the lightbox (`?payoff=`), a chip per dated curve.
 */

function tone(v: number | null): string {
  return v === null ? "muted" : v >= 0 ? "pnl-pos" : "pnl-neg";
}

const W = 760;
const H = 300;
const PAD = { l: 64, r: 16, t: 16, b: 30 };

/** The chart's SVG paths and scales; exported for its test. */
export function chartGeometry(n: ManagePayoffName, shown: number[]) {
  const curves = shown.map((i) => n.curves[i]).filter((c) => c && c.pnl);
  const ys = curves.flatMap((c) => c!.pnl!);
  const lo = Math.min(0, ...ys);
  const hi = Math.max(0, ...ys);
  const span = hi - lo || 1;
  const x0 = n.grid[0] ?? 0;
  const x1 = n.grid[n.grid.length - 1] ?? 1;
  const x = (s: number) => PAD.l + ((s - x0) / (x1 - x0 || 1)) * (W - PAD.l - PAD.r);
  const y = (v: number) => PAD.t + ((hi - v) / span) * (H - PAD.t - PAD.b);
  const path = (pnl: number[]) => pnl.map((v, i) => `${i === 0 ? "M" : "L"}${x(n.grid[i]!).toFixed(1)},${y(v).toFixed(1)}`).join(" ");
  return { x, y, lo, hi, x0, x1, path };
}

function PayoffChart({ n, dated }: { n: ManagePayoffName; dated: number }) {
  const live = useLiveMarks()?.payload.spots[n.underlying] ?? null;
  const expiry = n.curves.length - 1;
  const shown = dated === expiry ? [expiry] : [dated, expiry];
  const g = chartGeometry(n, shown);
  const ticks = [g.hi, (g.hi + g.lo) / 2, g.lo];
  const xticks = [0, 0.25, 0.5, 0.75, 1].map((f) => g.x0 + (g.x1 - g.x0) * f);
  const exp = n.curves[expiry]!;
  const date = n.curves[dated]!;
  return (
    <figure className="pay-figure">
      <svg viewBox={`0 0 ${W} ${H}`} className="pay-svg" role="img" aria-label={`${n.underlying} P&L by price`}>
        <line x1={PAD.l} x2={W - PAD.r} y1={g.y(0)} y2={g.y(0)} className="pay-zero" />
        {ticks.map((v) => (
          <text key={`y${v}`} x={PAD.l - 6} y={g.y(v) + 4} textAnchor="end" className="pay-axis">
            {fmtCash(Math.round(v))}
          </text>
        ))}
        {xticks.map((s) => (
          <text key={`x${s}`} x={g.x(s)} y={H - 8} textAnchor="middle" className="pay-axis">
            {s.toFixed(s >= 100 ? 0 : 2)}
          </text>
        ))}
        {exp.pnl ? <path d={g.path(exp.pnl)} className="pay-line is-expiry" /> : null}
        {dated !== expiry && date.pnl ? <path d={g.path(date.pnl)} className="pay-line is-date" /> : null}
        {n.breakevens.map((b) => (
          <line key={`be${b}`} x1={g.x(b)} x2={g.x(b)} y1={PAD.t} y2={H - PAD.b} className="pay-be" />
        ))}
        {n.spot !== null ? <line x1={g.x(n.spot)} x2={g.x(n.spot)} y1={PAD.t} y2={H - PAD.b} className="pay-spot" /> : null}
        {live !== null && live >= g.x0 && live <= g.x1 ? (
          <line x1={g.x(live)} x2={g.x(live)} y1={PAD.t} y2={H - PAD.b} className="pay-live" />
        ) : null}
      </svg>
      <figcaption className="pay-legend">
        <span className="pay-key is-expiry">{exp.label}</span>
        {dated !== expiry ? (
          <span className="pay-key is-date">
            {date.label}
            {date.pnl ? "" : ": not drawn, a leg has no vol"}
          </span>
        ) : null}
        <span className="pay-key is-spot">evaluation {n.spot?.toFixed(2) ?? "—"}</span>
        {live !== null ? <span className="pay-key is-live">live {live.toFixed(2)}</span> : null}
        {n.breakevens.length ? <span className="pay-key is-be">breakeven {n.breakevens.map((b) => b.toFixed(2)).join(", ")}</span> : null}
      </figcaption>
    </figure>
  );
}

function PayoffDialog({ names, index, onStep, onClose }: { names: ManagePayoffName[]; index: number; onStep: (to: number) => void; onClose: () => void }) {
  const n = names[index]!;
  const [dated, setDated] = useState(0);
  const pick = Math.min(dated, Math.max(0, n.curves.length - 1));
  return (
    <CardLightbox count={names.length} index={index} onStep={onStep} onClose={onClose}>
      {(nav) => (
        <LightboxFrame
          kicker={`payoff · ${n.model === "european" ? "European (cash-settled index)" : "American, early exercise"}`}
          title={n.underlying}
          chips={
            <>
              <span className={`chip ${tone(n.pnlNow)}`}>P&amp;L now {n.pnlNow === null ? "—" : fmtCash(n.pnlNow)}</span>
              <span className="chip">cost {n.cost === null ? "—" : fmtCash(n.cost)}</span>
            </>
          }
          tabs={[{ id: "payoff", label: "payoff" }]}
          tab="payoff"
          onTab={() => undefined}
          position={nav.position}
          onPrev={nav.prev}
          onNext={nav.next}
          onClose={onClose}
        >
          {n.why ? (
            <p className="muted">no payoff: {n.why}</p>
          ) : (
            <>
              <div className="pay-dates" role="group" aria-label="Value on">
                {n.curves.map((c, i) => (
                  <button key={c.label} type="button" className={i === pick ? "ldg-win is-on" : "ldg-win"} aria-pressed={i === pick} onClick={() => setDated(i)}>
                    {c.label}
                  </button>
                ))}
              </div>
              <PayoffChart n={n} dated={pick} />
              <div className="table-scroll">
                <table className="ldg-table">
                  <thead>
                    <tr>
                      <th>leg</th>
                      <th className="num">mid</th>
                      <th className="num">implied vol</th>
                    </tr>
                  </thead>
                  <tbody>
                    {n.legs.map((l) => (
                      <tr key={l.leg}>
                        <td>{l.leg}</td>
                        <td className="num">{l.mid === null ? "—" : l.mid.toFixed(2)}</td>
                        <td className="num">{l.iv === null ? "no vol prices it" : `${(l.iv * 100).toFixed(1)}%`}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <p className="muted pay-note">
                a model from the evaluation's marks: each vol solved from its leg's mid, rate {n.rate === null ? "—" : `${(n.rate * 100).toFixed(1)}%`},
                dividend yield {n.divYield === null ? "0" : `${(n.divYield * 100).toFixed(2)}%`} ({n.yieldSource ?? "—"}). At expiry it is
                arithmetic; before it, an estimate.
              </p>
            </>
          )}
        </LightboxFrame>
      )}
    </CardLightbox>
  );
}

/** The strip of held names, each with manage's P&L now; one opens its payoff. */
export function PayoffStrip({ payoff }: { payoff: ManagePayoff }) {
  const lightbox = useOpenParam("payoff");
  const names = payoff.names;
  if (names.length === 0) return null;
  const order = names.map((n) => n.underlying);
  const index = lightbox.open === null ? -1 : order.indexOf(lightbox.open);
  return (
    <section className="pay-board" aria-label="Payoff">
      <div className="att-head">
        <span className="att-title">Payoff</span>
        <span className="muted">each name held: P&amp;L across its price, at expiry and before</span>
      </div>
      <div className="pay-names">
        {names.map((n) => (
          <button key={n.underlying} type="button" className="pay-name" onClick={() => lightbox.show(n.underlying)} aria-label={`Open ${n.underlying}'s payoff`}>
            <span className="att-name">{n.underlying}</span>
            <span className={tone(n.pnlNow)}>{n.pnlNow === null ? "—" : fmtCash(n.pnlNow)}</span>
            {n.breakevens.length ? <span className="muted">BE {n.breakevens.map((b) => b.toFixed(2)).join(" / ")}</span> : null}
          </button>
        ))}
      </div>
      {index >= 0 ? <PayoffDialog names={names} index={index} onStep={(to) => lightbox.step(order[to] ?? "")} onClose={lightbox.close} /> : null}
    </section>
  );
}
