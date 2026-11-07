import { useState } from "react";
import type { ManageModelResult, ManagePayoff, ManagePayoffName } from "@console/shared";
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
export function chartGeometry(n: ManagePayoffName, shown: number[], extra: number[][] = []) {
  const curves = shown.map((i) => n.curves[i]).filter((c) => c && c.pnl);
  const ys = [...curves.flatMap((c) => c!.pnl!), ...extra.flat()];
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

/** `before`: the name's payoff without the modelled trade, its expiry curve drawn faint behind. */
function PayoffChart({ n, dated, before = null }: { n: ManagePayoffName; dated: number; before?: ManagePayoffName | null }) {
  const live = useLiveMarks()?.payload.spots[n.underlying] ?? null;
  const expiry = n.curves.length - 1;
  const shown = dated === expiry ? [expiry] : [dated, expiry];
  const beforePnl = before?.curves[before.curves.length - 1]?.pnl ?? null;
  const g = chartGeometry(n, shown, beforePnl ? [beforePnl] : []);
  const beforePath =
    before && beforePnl
      ? beforePnl.map((v, i) => `${i === 0 ? "M" : "L"}${g.x(before.grid[i]!).toFixed(1)},${g.y(v).toFixed(1)}`).join(" ")
      : null;
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
        {beforePath ? <path d={beforePath} className="pay-line is-before" /> : null}
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
        {beforePath ? <span className="pay-key is-before">without the trade, at expiry</span> : null}
        <span className="pay-key is-expiry">{beforePath ? `with it, ${exp.label}` : exp.label}</span>
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

/** One proposed leg as the form holds it. */
export interface DraftLeg {
  side: "sell" | "buy";
  qty: string;
  expiry: string;
  strike: string;
  right: "P" | "C";
  price: string;
}

/** The leg text manage reads ("-1 2026-12-18 46P @ 1.05"), or null when a field is not filled in. */
export function legText(d: DraftLeg): string | null {
  const qty = Number(d.qty);
  const strike = Number(d.strike);
  if (!(qty > 0) || !(strike > 0) || !/^\d{4}-\d{2}-\d{2}$/.test(d.expiry)) return null;
  const price = d.price.trim() === "" ? null : Number(d.price);
  if (price !== null && !(price >= 0)) return null;
  return `${d.side === "sell" ? "-" : "+"}${qty} ${d.expiry} ${strike}${d.right}${price === null ? "" : ` @ ${price}`}`;
}

function pct(v: number | null, digits = 1): string {
  return v === null ? "—" : `${(v * 100).toFixed(digits)}%`;
}

/** The model a trade tab: legs to add, manage's model of them on the name (`/api/manage/model`). */
function ModelPanel({ n }: { n: ManagePayoffName }) {
  const blank = (): DraftLeg => ({ side: "sell", qty: "1", expiry: n.curves[n.curves.length - 1]?.date ?? "", strike: "", right: "P", price: "" });
  const [rows, setRows] = useState<DraftLeg[]>([blank()]);
  const [result, setResult] = useState<ManageModelResult | null>(null);
  const [busy, setBusy] = useState(false);
  const texts = rows.map(legText);
  const ready = texts.every((t) => t !== null);
  const set = (i: number, patch: Partial<DraftLeg>) => setRows(rows.map((r, j) => (j === i ? { ...r, ...patch } : r)));
  const run = async () => {
    setBusy(true);
    const q = new URLSearchParams({ underlying: n.underlying });
    for (const t of texts) if (t) q.append("leg", t);
    try {
      const res = await fetch(`/api/manage/model?${q.toString()}`);
      setResult((await res.json()) as ManageModelResult);
    } catch (e) {
      setResult({ ok: false, error: String(e) } as ManageModelResult);
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="mdl">
      <p className="muted pay-note">
        legs to add to {n.underlying}, priced against the evaluation's snapshot: at the price you give, or at the name's IV index.
        Nothing is sent to the broker; the console has no order path.
      </p>
      {rows.map((r, i) => (
        <div key={i} className="mdl-row">
          <label>
            <span>side</span>
            <select value={r.side} onChange={(e) => set(i, { side: e.target.value as DraftLeg["side"] })}>
              <option value="sell">sell</option>
              <option value="buy">buy</option>
            </select>
          </label>
          <label>
            <span>contracts</span>
            <input type="number" min="1" value={r.qty} onChange={(e) => set(i, { qty: e.target.value })} />
          </label>
          <label>
            <span>expiry</span>
            <input type="date" value={r.expiry} onChange={(e) => set(i, { expiry: e.target.value })} />
          </label>
          <label>
            <span>strike</span>
            <input type="number" step="0.5" value={r.strike} onChange={(e) => set(i, { strike: e.target.value })} />
          </label>
          <label>
            <span>right</span>
            <select value={r.right} onChange={(e) => set(i, { right: e.target.value as DraftLeg["right"] })}>
              <option value="P">put</option>
              <option value="C">call</option>
            </select>
          </label>
          <label>
            <span>price (optional)</span>
            <input type="number" step="0.01" min="0" value={r.price} placeholder="IV index" onChange={(e) => set(i, { price: e.target.value })} />
          </label>
          {rows.length > 1 ? (
            <button type="button" className="ldg-win" onClick={() => setRows(rows.filter((_, j) => j !== i))} aria-label={`Remove leg ${i + 1}`}>
              remove
            </button>
          ) : null}
        </div>
      ))}
      <div className="mdl-actions">
        {rows.length < 6 ? (
          <button type="button" className="ldg-win" onClick={() => setRows([...rows, blank()])}>
            add a leg
          </button>
        ) : null}
        <button type="button" className="ldg-win is-on" disabled={!ready || busy} onClick={() => void run()}>
          {busy ? "modelling…" : "model it"}
        </button>
      </div>
      {result === null ? null : !result.ok ? (
        <p className="pnl-neg">cannot model: {result.error}</p>
      ) : (
        <ModelResult r={result} />
      )}
    </div>
  );
}

function ModelResult({ r }: { r: ManageModelResult }) {
  const after = r.after;
  return (
    <>
      <div className="ldg-tiles mdl-tiles">
        <div className="ldg-tile">
          <span className="ldg-tile-label">{(r.net ?? 0) >= 0 ? "credit" : "debit"}</span>
          <span className={`ldg-tile-value ${tone(r.net)}`}>{r.net === null ? "—" : fmtCash(r.net)}</span>
        </div>
        <div className="ldg-tile">
          <span className="ldg-tile-label">worst case at expiry</span>
          <span className="ldg-tile-value pnl-neg">{r.unbounded ? "unbounded" : r.worstCase === null ? "—" : fmtCash(r.worstCase)}</span>
          <span className="ldg-tile-sub">{r.unbounded ? "a short call with nothing covering it" : r.worstCaseAt === null ? "" : `at ${r.worstCaseAt.toFixed(2)}`}</span>
        </div>
        <div className="ldg-tile">
          <span className="ldg-tile-label">delta · theta per day</span>
          <span className="ldg-tile-value">
            {r.delta === null ? "—" : r.delta.toFixed(1)} · {r.thetaDay === null ? "—" : fmtCash(r.thetaDay)}
          </span>
        </div>
        <div className="ldg-tile">
          <span className="ldg-tile-label">implied vs 30-day realised</span>
          <span className="ldg-tile-value">
            {pct(r.ivIndex)} vs {pct(r.hv30Day)}
          </span>
        </div>
      </div>
      {after && !after.why ? <PayoffChart n={after} dated={after.curves.length - 1} before={r.before} /> : null}
      <div className="table-scroll">
        <table className="ldg-table">
          <thead>
            <tr>
              <th>leg</th>
              <th className="num">price</th>
              <th>from</th>
              <th className="num">vol</th>
              <th className="num">delta</th>
              <th className="num">theta per day</th>
            </tr>
          </thead>
          <tbody>
            {r.legs.map((l) => (
              <tr key={l.leg}>
                <td>{l.leg}</td>
                <td className="num">{l.price.toFixed(2)}</td>
                <td>{l.priceSource}</td>
                <td className="num">{pct(l.iv)}</td>
                <td className="num">{l.delta === null ? "—" : l.delta.toFixed(2)}</td>
                <td className="num">{l.thetaDay === null ? "—" : fmtCash(l.thetaDay)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <ul className="mdl-caps">
        {r.caps.map((c) => (
          <li key={c.label} className={c.over ? "is-over" : ""}>
            <span>{c.label}</span>
            <span className="mdl-bar">
              <span style={{ width: `${Math.min(100, c.value === null || !c.cap ? 0 : (c.value / c.cap) * 100)}%` }} />
            </span>
            <span className="mdl-num">
              {pct(c.value)} of {pct(c.cap, 1)}
              {c.over ? " · over" : ""}
            </span>
          </li>
        ))}
      </ul>
      <p className="muted pay-note">
        caps are the ruleset's candidate caps ({r.accountBasis ?? "—"} account); implied against realised vol is the edge a short option has,
        shown, never ranked on.
      </p>
    </>
  );
}

type DialogTab = "payoff" | "model";

function PayoffDialog({ names, index, onStep, onClose }: { names: ManagePayoffName[]; index: number; onStep: (to: number) => void; onClose: () => void }) {
  const n = names[index]!;
  const [dated, setDated] = useState(0);
  const [tab, setTab] = useState<DialogTab>("payoff");
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
          tabs={[
            { id: "payoff", label: "payoff" },
            { id: "model", label: "model a trade" },
          ]}
          tab={tab}
          onTab={setTab}
          position={nav.position}
          onPrev={nav.prev}
          onNext={nav.next}
          onClose={onClose}
        >
          {tab === "model" ? (
            <ModelPanel key={n.underlying} n={n} />
          ) : n.why ? (
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

/** The strip of held names, each with manage's P&L now; one opens its payoff. `scope` (the wheel's
 *  names) keeps only those. */
export function PayoffStrip({ payoff, scope = null }: { payoff: ManagePayoff; scope?: string[] | null }) {
  const lightbox = useOpenParam("payoff");
  const names = scope === null ? payoff.names : payoff.names.filter((n) => scope.includes(n.underlying));
  if (names.length === 0) return null;
  const order = names.map((n) => n.underlying);
  const index = lightbox.open === null ? -1 : order.indexOf(lightbox.open);
  return (
    <section className="pay-board" aria-label="Payoff">
      <div className="att-head">
        <span className="att-title">Payoff</span>
        <span className="muted">each {scope === null ? "name" : "wheel name"} held: P&amp;L across its price, at expiry and before</span>
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
