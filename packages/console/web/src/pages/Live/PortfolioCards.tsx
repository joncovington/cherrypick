import type { ReactNode } from "react";
import type { ManageBand, ManageGuidance, ManageHedgeSuggestion, ManagePortfolio } from "@console/shared";
import { Card } from "../../components/DataTable";
import { Bullet } from "../../components/grid/Bullet";
import { DivergingBars } from "../../components/grid/DivergingBars";
import { TileGrid } from "../../components/performance/TileGrid";
import { fmtCash, fmtMoney } from "../../lib/format";
import { Tile } from "../Flies/PerformanceTab";
import { IncomeCard } from "./IncomeCard";

/**
 * The account read as one book (packages/manage `portfolio.py`), as cards: a dial for beta-weighted
 * delta against its band, meters for theta and the break-even move, the stress line, where the delta
 * and theta come from, the guidance, and a sized hedge when the delta band is breached.
 *
 * It computes nothing. Every band and every "inside" is the reading's own (`bands`), so the dial
 * draws the band manage judged by and cannot disagree with it. The one tone that is not a number's
 * sign is in-band against out-of-band, and that is a flag manage set. Long and short delta carry no
 * tone: neither sign is a verdict. Every figure is modelled (Black-Scholes at each leg's own IV), and
 * the cards say so.
 */

/** A fraction as a percentage, `0.31%`. */
function pctOf(v: number | null, digits = 2): string {
  return v === null ? "—" : `${(v * 100).toFixed(digits)}%`;
}

function signed(v: number | null, digits = 1): string {
  return v === null ? "—" : `${v >= 0 ? "+" : "−"}${Math.abs(v).toFixed(digits)}`;
}

/** The chip a band reading earns. Null (nothing to place) reads as such, never as "in band". */
export function BandChip({ inside }: { inside: boolean | null }) {
  if (inside === null) return <span className="chip muted">no reading</span>;
  return inside ? <span className="chip">in band</span> : <span className="chip pf-out">outside band</span>;
}

/* ---------------------------------------------------------------- the dial */

const D = { cx: 110, cy: 100, r: 80, stroke: 14 };

function polar(r: number, theta: number): [number, number] {
  const rad = (theta * Math.PI) / 180;
  return [D.cx - r * Math.cos(rad), D.cy - r * Math.sin(rad)];
}

function arc(a: number, b: number): string {
  const [x1, y1] = polar(D.r, a);
  const [x2, y2] = polar(D.r, b);
  return `M ${x1.toFixed(2)} ${y1.toFixed(2)} A ${D.r} ${D.r} 0 0 1 ${x2.toFixed(2)} ${y2.toFixed(2)}`;
}

/** A round number at or above `v`, so the dial's ends read as a scale rather than as a measurement. */
function niceCeil(v: number): number {
  if (v <= 0) return 1;
  const step = 10 ** Math.floor(Math.log10(v));
  return [1, 2, 2.5, 5, 10].map((m) => m * step).find((n) => n >= v) ?? 10 * step;
}

/**
 * A half-circle dial for a figure against a band: the band as a segment on the track and a needle at
 * the value. `signedScale` centres the scale on zero, so long and short read with equal weight (and
 * marks zero); otherwise the scale runs from zero up. The needle clamps to the scale; the number
 * under it never does.
 */
export function BandDial({
  band,
  unit,
  label,
  signedScale = true,
  fmt = (v: number) => signed(v),
  tick = (v: number) => signed(v, 0),
}: {
  band: ManageBand;
  unit: string;
  label: string;
  signedScale?: boolean;
  fmt?: (v: number) => string;
  tick?: (v: number) => string;
}) {
  const { low, high, value } = band;
  if (value === null || low === null || high === null) return <p className="muted">no {label} to place</p>;
  const reach = niceCeil(Math.max(Math.abs(low), Math.abs(high), Math.abs(value)) * (signedScale ? 1.15 : 1.4));
  const lo = signedScale ? -reach : 0;
  const theta = (v: number) => ((Math.min(Math.max(v, lo), reach) - lo) / (reach - lo)) * 180;
  const [nx, ny] = polar(D.r - 20, theta(value));
  const [lx, ly] = polar(D.r + 14, theta(low));
  const [hx, hy] = polar(D.r + 14, theta(high));
  const [zx1, zy1] = polar(D.r - D.stroke / 2 - 3, 90);
  const [zx2, zy2] = polar(D.r + D.stroke / 2 + 3, 90);
  const out = band.inside === false;
  const said = `${label} ${fmt(value)} ${unit} · band ${fmt(low)} to ${fmt(high)}`;
  return (
    <div className="pf-dial">
      <svg viewBox="0 0 220 122" role="img" aria-label={said}>
        <title>{said}</title>
        <path d={arc(0, 180)} className="ts-track" strokeWidth={D.stroke} fill="none" strokeLinecap="round" />
        <path d={arc(theta(low), theta(high))} className="pf-dial-band" strokeWidth={D.stroke} fill="none" />
        {signedScale && <line x1={zx1} y1={zy1} x2={zx2} y2={zy2} className="pf-dial-zero" strokeWidth={1.5} />}
        {/* A band edge on a scale end is already labelled by that end. */}
        {low !== lo && (
          <text x={lx} y={ly} className="ts-tick" textAnchor="middle" dominantBaseline="middle">
            {tick(low)}
          </text>
        )}
        {high !== reach && (
          <text x={hx} y={hy} className="ts-tick" textAnchor="middle" dominantBaseline="middle">
            {tick(high)}
          </text>
        )}
        <text x={D.cx - D.r} y={D.cy + 18} className="ts-tick" textAnchor="middle">
          {tick(lo)}
        </text>
        <text x={D.cx + D.r} y={D.cy + 18} className="ts-tick" textAnchor="middle">
          {tick(reach)}
        </text>
        <line x1={D.cx} y1={D.cy} x2={nx} y2={ny} className={out ? "pf-needle-out" : "ts-needle"} strokeWidth={2.5} strokeLinecap="round" />
        <circle cx={D.cx} cy={D.cy} r={4.5} className={out ? "pf-needle-out" : "ts-needle"} />
      </svg>
      <div className={`ts-gauge-value ${out ? "pf-out-text" : ""}`}>
        {fmt(value)} <span className="pf-unit">{unit}</span>
      </div>
    </div>
  );
}

/* ---------------------------------------------------------------- the cards */

function DeltaCard({ p }: { p: ManagePortfolio }) {
  const band = p.bands?.delta ?? null;
  return (
    <Card title="beta-weighted delta" controls={band && <BandChip inside={band.inside} />}>
      {band ? <BandDial band={band} unit={`${p.benchmark ?? "SPY"} sh`} label="beta-weighted delta" /> : <p className="muted">no band recorded</p>}
      <p className="muted pf-caption">
        {fmtCash(p.bwdDollarsPer1pct)} per 1% {p.benchmark ?? "SPY"} move · band{" "}
        {band?.basis === "theta" ? "scaled by theta" : "scaled by net liq (no theta to scale by)"} · the broker's beta
      </p>
    </Card>
  );
}

/** A band's fractions as percentages, for a dial whose ticks read "0.3" rather than "0.003". */
export function asPercent(b: ManageBand): ManageBand {
  const x = (v: number | null) => (v === null ? null : v * 100);
  return { low: x(b.low), high: x(b.high), value: x(b.value), inside: b.inside };
}

function ThetaCard({ p }: { p: ManagePortfolio }) {
  const band = p.bands?.theta ?? null;
  const short = (v: number) => (v === 0 ? "0" : v.toFixed(v < 1 ? 2 : 1).replace(/0+$/, "").replace(/\.$/, ""));
  return (
    <Card title="theta" controls={band && <BandChip inside={band.inside} />}>
      {band ? (
        <BandDial
          band={asPercent(band)}
          unit="% of net liq a day"
          label="theta"
          signedScale={false}
          fmt={(v) => v.toFixed(2)}
          tick={short}
        />
      ) : (
        <p className="muted">no band recorded</p>
      )}
      <p className="muted pf-caption">
        {fmtMoney(p.theta)} a day · band {pctOf(band?.low ?? null)}–{pctOf(band?.high ?? null)} · theta is paid for by gamma, so
        more is not more edge
      </p>
    </Card>
  );
}

function BreakevenCard({ p }: { p: ManagePortfolio }) {
  const be = p.breakeven;
  const realised = p.realisedMove !== null && p.benchmarkSpot ? p.realisedMove / p.benchmarkSpot : null;
  const expected = p.expectedMovePct;
  if (!be || be.pct === null) {
    return (
      <Card title="break-even move">
        <p className="muted">theta and gamma do not oppose each other, so there is no break-even move</p>
      </Card>
    );
  }
  const max = Math.max(be.pct, expected ?? 0, realised ?? 0) * 1.3;
  const short = be.kind === "short_gamma";
  return (
    <Card title="break-even move">
      <div className="pf-hero">
        {pctOf(be.pct)}
        <span className="pf-unit"> of {p.benchmark ?? "SPY"}</span>
      </div>
      <Bullet min={0} max={max} value={be.pct} marker={expected} label={`break-even ${pctOf(be.pct)}, implied move ${pctOf(expected)}`} />
      <p className="muted pf-caption">
        {short
          ? `a ${pctOf(be.pct)} move in a day wipes out a day's theta`
          : `the book needs a ${pctOf(be.pct)} move in a day to earn back its theta`}{" "}
        · tick: {p.benchmark ?? "SPY"}'s implied one-day move {pctOf(expected)}
        {realised !== null ? ` · realised (30-day) ${pctOf(realised)}` : ""}
      </p>
    </Card>
  );
}

function StressCard({ p }: { p: ManagePortfolio }) {
  const tiles = [
    ...p.stress.map((s) => (
      <Tile
        key={s.shock}
        label={`${p.benchmark ?? "SPY"} ${s.shock > 0 ? "+" : "−"}${Math.abs(s.shock * 100).toFixed(0)}%`}
        value={fmtCash(s.pnl)}
        tone={s.pnl === null ? "dim" : s.pnl >= 0 ? "pos" : "neg"}
        title={`${pctOf(s.pctNetLiq, 1)} of net liq; each name moved by its beta, IV and time held still${
          s.leftOut.length ? `; left out (no beta): ${s.leftOut.join(", ")}` : ""
        }`}
      />
    )),
    <Tile key="bp" label="buying power used" value={pctOf(p.buyingPowerUsedPct, 0)} title={fmtMoney(p.buyingPowerUsed)} />,
    <Tile key="vega" label="vega (30-day weighted)" value={fmtCash(p.vega30)} title="dollars per volatility point, front months weighted up" />,
    <Tile
      key="em"
      label={`${p.benchmark ?? "SPY"} implied 1-day move`}
      value={p.expectedMove === null ? "—" : `±${p.expectedMove.toFixed(2)}`}
      title="points, from its implied volatility index, per calendar day"
    />,
    <Tile key="kind" label="account" value={p.accountKind.typeName ?? p.accountKind.marginOrCash ?? "—"} title={p.accountKind.marginOrCash ?? "kind unknown"} />,
  ];
  return (
    <Card title="if the market moves">
      {/* Six tiles: the two shocks manage writes by default, then four that frame them. A ruleset with
          other shocks would change the count, so the row is cut to six rather than left ragged. */}
      <TileGrid count={6}>{tiles.slice(0, 6)}</TileGrid>
      <p className="muted pf-caption">modelled repricing at each name's beta, not a forecast</p>
    </Card>
  );
}

function ContributionCard({ p, figure }: { p: ManagePortfolio; figure: "bwd" | "theta" }) {
  const rows = [...p.underlyings]
    .filter((u) => u[figure] !== null)
    .sort((a, b) => Math.abs(b[figure] ?? 0) - Math.abs(a[figure] ?? 0))
    .slice(0, 12)
    .map((u) => ({
      label: u.betaDoubt && figure === "bwd" ? `${u.underlying} ⚠` : u.underlying,
      value: u[figure],
      title: `${u.underlying}: beta ${u.beta === null ? "—" : u.beta.toFixed(2)} · ${pctOf(figure === "bwd" ? u.bwdShare : u.thetaShare, 0)} of the book's gross ${
        figure === "bwd" ? "delta" : "theta"
      }${u.betaDoubt ? ` · ${u.betaDoubt}` : ""}`,
    }));
  return (
    <Card title={figure === "bwd" ? "where the delta comes from" : "where the theta comes from"}>
      <DivergingBars
        rows={rows}
        tone="none"
        format={(v) => (figure === "bwd" ? `${signed(v)} sh` : `${fmtCash(v)}/day`)}
        emptyText="no modelled positions"
      />
      {figure === "bwd" && p.underlyings.some((u) => u.betaDoubt) && (
        <p className="muted pf-caption">⚠ the broker's beta disagrees with its own correlation for this name, so its delta may be misstated</p>
      )}
    </Card>
  );
}

/**
 * Our delta and theta against the same sums on the broker's own greeks, over the same legs and betas
 * (`broker_check`). It decides nothing: it is the record of how far our model sits from the broker's,
 * kept before the bands are trusted. Gaps carry no tone, since neither side is the right answer.
 */
export function BrokerCheckCard({ p }: { p: ManagePortfolio }) {
  const c = p.brokerCheck;
  if (!c) return null;
  const sh = (v: number | null) => (v === null ? "—" : `${signed(v)} sh`);
  return (
    <Card title="against the broker's model">
      <TileGrid count={6}>
        <Tile label="delta, ours" value={sh(c.bwdOurs)} />
        <Tile label="delta, broker's greeks" value={sh(c.bwdBroker)} />
        <Tile label="delta gap" value={sh(c.bwdGap)} title="ours less the broker's" />
        <Tile label="theta, ours" value={fmtCash(c.thetaOurs)} />
        <Tile label="theta, broker's greeks" value={fmtCash(c.thetaBroker)} />
        <Tile label="theta gap" value={fmtCash(c.thetaGap)} title="ours less the broker's, a day" />
      </TileGrid>
      <DivergingBars
        rows={c.underlyings.slice(0, 8).map((u) => ({
          label: u.underlying,
          value: u.bwdGap,
          title: `${u.underlying}: ${u.legs} legs · theta gap ${fmtCash(u.thetaGap)}/day`,
        }))}
        tone="none"
        format={(v) => `${signed(v)} sh`}
        emptyText="no legs both models priced"
      />
      <p className="muted pf-caption">
        {c.legsCompared} legs on both models, same betas · the broker's greeks are American-exercise with dividends, ours
        Black-Scholes at each leg's own IV
        {c.legsLeftOut.length ? ` · left out of both (one model only): ${c.legsLeftOut.join(", ")}` : ""}
      </p>
    </Card>
  );
}

// What each guidance rule is about, for its chip. A rule this page does not know is named from its id.
const GUIDANCE_TOPIC: Record<string, string> = {
  "portfolio.theta": "theta",
  "portfolio.delta": "delta",
  "portfolio.breakeven": "break-even",
  "portfolio.buying_power": "buying power",
  "portfolio.concentration": "concentration",
};

function guidanceTopic(rule: string): string {
  return GUIDANCE_TOPIC[rule] ?? rule.replace(/^portfolio\./, "").replace(/_/g, " ");
}

/** One guidance item as its own small card: the topic as an amber chip (every item is a review), the
 *  rule's confidence, its reason, and what drives it. */
function GuidanceItem({ g }: { g: ManageGuidance }) {
  return (
    <section className="md-card" title={g.rule}>
      <header>
        <span className="chip chip-warn">{guidanceTopic(g.rule)}</span>
        {g.confidence !== null ? <span className="muted pf-guide-conf">confidence {g.confidence}</span> : null}
      </header>
      <p className="pf-guide-reason">{g.reason}</p>
      {g.drivers.length > 0 && (
        <ul className="pf-guide-drivers">
          {g.drivers.map((d) => (
            <li key={d.underlying}>
              <span>{d.underlying}</span>
              <span>{signed(d.value)}</span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function GuidanceCard({ p }: { p: ManagePortfolio }) {
  return (
    <Card title="portfolio guidance">
      {p.guidance.length === 0 ? (
        <p className="muted">nothing outside its band today</p>
      ) : (
        <div className="pf-guide-grid">
          {p.guidance.map((g) => (
            <GuidanceItem key={g.rule} g={g} />
          ))}
        </div>
      )}
      {!p.complete && (
        <p className="muted pf-caption">
          partial: {[...p.unmodelledLegs.map((l) => `${l} (not modelled)`), ...p.noBeta.map((u) => `${u} (no beta)`)].join(", ") || "no benchmark price"}
        </p>
      )}
      <p className="muted pf-caption">retail heuristics at confidence 40: no published evidence stands behind these bands</p>
    </Card>
  );
}

function HedgeSuggestion({ s }: { s: ManageHedgeSuggestion }) {
  if (s.error) {
    return (
      <div className="pf-hedge">
        <strong>{s.structure ?? s.kind}</strong> <span className="muted">· {s.error}</span>
      </div>
    );
  }
  const a = s.after;
  return (
    <div className="pf-hedge">
      <div>
        <strong>
          {s.structure} × {s.units}
        </strong>{" "}
        · {s.priceLabel} at mid (natural {s.natural === null ? "—" : `${Math.abs(s.natural).toFixed(2)} ${s.natural >= 0 ? "cr" : "db"}`}) ·
        cash {fmtCash(s.cash)} · max loss {fmtMoney(s.maxLoss)}
      </div>
      <ul>
        {s.legs.map((l) => (
          <li key={l.label}>
            {l.quantity > 0 ? "buy" : "sell"} {Math.abs(l.quantity)} × {l.label}{" "}
            <span className="muted">
              ({l.bid ?? "—"} / {l.ask ?? "—"}, Δ {l.delta === null ? "—" : l.delta.toFixed(2)})
            </span>
          </li>
        ))}
      </ul>
      {a && (
        <div className="muted">
          after: delta {signed(s.bwdChange)} → {signed(a.bwd)} sh {a.inBand ? "(in band)" : "(still outside)"} · theta{" "}
          {fmtCash(s.thetaChange)} → {fmtMoney(a.theta)}/day · break-even {pctOf(a.breakevenPct)} · buying power{" "}
          {pctOf(a.buyingPowerUsedPct, 0)}
        </div>
      )}
      {s.pastCap && (
        <div className="pf-out-text">
          past the buying-power cap: offered anyway, because a debit spread only lowers the book's worst case
        </div>
      )}
    </div>
  );
}

function HedgeCard({ p }: { p: ManagePortfolio }) {
  const h = p.hedges;
  if (!h) return null;
  return (
    <Card title="a hedge, sized">
      <p className="muted pf-caption" style={{ marginTop: 0 }}>
        delta {signed(p.bwd)} sh, needs {signed(h.need)} to reach {signed(h.target)} · {h.root ?? "—"} {h.expiration ?? ""}
        {h.dte !== null ? ` (${h.dte} days)` : ""} · priced at mid, which is not a fill · nothing is placed
      </p>
      {h.error ? <p className="muted">{h.error}</p> : h.suggestions.map((s) => <HedgeSuggestion key={s.kind} s={s} />)}
    </Card>
  );
}

/**
 * Every portfolio card, in pairs of matching height. Nothing when the evaluation predates them.
 * The guidance follows the four headline readings it is judged from (delta, theta, break-even and
 * the stress row) -- 2026-10-19: moved up from below the scenarios, where it was read last -- and the
 * breakdowns, `scenarios` (ScenarioCards.tsx) and the hedge come after it.
 */
export function PortfolioCards({ p, scenarios }: { p: ManagePortfolio | null; scenarios?: ReactNode }) {
  if (!p) return null;
  return (
    <>
      <div className="cards cards-pairs">
        <DeltaCard p={p} />
        <ThetaCard p={p} />
        <BreakevenCard p={p} />
        <StressCard p={p} />
      </div>
      <GuidanceCard p={p} />
      <div className="cards cards-pairs">
        <ContributionCard p={p} figure="bwd" />
        <ContributionCard p={p} figure="theta" />
      </div>
      <BrokerCheckCard p={p} />
      {scenarios}
      <HedgeCard p={p} />
      <IncomeCard c={p.candidates} />
    </>
  );
}
