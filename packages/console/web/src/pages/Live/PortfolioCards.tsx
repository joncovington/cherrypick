import type { ReactNode } from "react";
import type { ManageBand, ManageGuidance, ManageGuidanceFigure, ManageLimits, ManagePortfolio, ManagePremium } from "@console/shared";
import { Card } from "../../components/DataTable";
import { DivergingBars } from "../../components/grid/DivergingBars";
import { useColumns } from "../../components/columns";
import { TileGrid } from "../../components/performance/TileGrid";
import { fmtCash, fmtMoney } from "../../lib/format";
import { Tile } from "../Flies/PerformanceTab";
import { limitChip, limitText, limitValue } from "./LimitsCard";
import { RiskGraph } from "./ScenarioCards";
import { TradeCards, hedgeModel } from "./TradeCard";
import { BoxButton, BoxButtons } from "./portfolioBoxContext";
import { HelpSection, HelpWarn, type CardHelpContent } from "../../components/CardHelp";
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

/** A band's fractions as percentages, for a dial whose ticks read "0.3" rather than "0.003". */
export function asPercent(b: ManageBand): ManageBand {
  const x = (v: number | null) => (v === null ? null : v * 100);
  return { low: x(b.low), high: x(b.high), value: x(b.value), inside: b.inside };
}

/**
 * The decimal places that keep a reading apart from its bounds: `base`, or more until the value no
 * longer prints the same as a bound it does not equal. A theta of 0.304% outside a 0.30% ceiling read
 * "0.30" under an "outside band" chip (2026-10-21). Capped at four more places.
 */
export function placesApart(value: number | null, bounds: Array<number | null>, base = 2): number {
  if (value === null) return base;
  for (let d = base; d < base + 4; d++) {
    if (bounds.every((b) => b === null || b === value || b.toFixed(d) !== value.toFixed(d))) return d;
  }
  return base + 4;
}

/** A dial beside its figures once the card is wide enough; stacked above them on a phone. */
function DialBeside({ dial, children }: { dial: ReactNode; children: ReactNode }) {
  return (
    <div className="pf-dwrap">
      <div className="pf-dv">{dial}</div>
      <div className="pf-dside">{children}</div>
    </div>
  );
}

/**
 * Income: theta against its band, beside the premium pace (theta against its target). The months
 * collected and kept, and theta over time, open from it.
 */
export function IncomeParentCard({ p, premium }: { p: ManagePortfolio; premium: ManagePremium | null }) {
  const band = p.bands?.theta ?? null;
  const short = (v: number) => (v === 0 ? "0" : v.toFixed(v < 1 ? 2 : 1).replace(/0+$/, "").replace(/\.$/, ""));
  const pctBand = band ? asPercent(band) : null;
  const places = pctBand ? placesApart(pctBand.value, [pctBand.low, pctBand.high]) : 2;
  const target = premium?.target ?? null;
  const ratio = target?.ratio ?? null;
  return (
    <Card title="income" className="pf-cq" help={INCOME_HELP} controls={band && <BandChip inside={band.inside} />}>
      <DialBeside
        dial={
          pctBand ? (
            <BandDial band={pctBand} unit="% of net liq per day" label="theta" signedScale={false} fmt={(v) => v.toFixed(places)} tick={short} />
          ) : (
            <p className="muted">no band recorded</p>
          )
        }
      >
        <div className="pf-hero">
          {fmtMoney(p.theta)}
          <span className="pf-unit"> theta per day</span>
        </div>
        {target && target.dollars !== null ? (
          <>
            <div className="pf-meter" title={`theta against the premium target of ${fmtMoney(target.dollars)} per day`}>
              <span style={{ width: `${Math.min(100, Math.max(0, (ratio ?? 0) * 100))}%` }} />
            </div>
            <p className="muted pf-caption">
              {ratio === null ? "—" : `${(ratio * 100).toFixed(0)}%`} of the {fmtMoney(target.dollars)} per day target (
              {pctOf(target.pctNlv)} of net liq)
            </p>
          </>
        ) : null}
        <p className="muted pf-caption">
          {p.thetaExFront !== null && p.frontDte !== null
            ? `${fmtMoney(p.thetaExFront)} per day without legs ${p.frontDte} days or less from expiry`
            : `${fmtMoney(p.theta)} per day`}{" "}
          · band {pctOf(band?.low ?? null, places)}–{pctOf(band?.high ?? null, places)}
        </p>
      </DialBeside>
      <BoxButtons>
        <BoxButton id="overTime">over time</BoxButton>
        <BoxButton id="pace">premium by month</BoxButton>
      </BoxButtons>
    </Card>
  );
}

/**
 * Direction: beta-weighted delta against its band, what a 1% move is worth, and the sized hedge's
 * headline while delta is outside the band (manage sizes one only then).
 */
export function DirectionCard({ p }: { p: ManagePortfolio }) {
  const band = p.bands?.delta ?? null;
  const places = band ? placesApart(band.value, [band.low, band.high], 1) : 1;
  const first = p.hedges?.suggestions.find((s) => !s.error) ?? null;
  return (
    <Card title="direction" className="pf-cq" help={directionHelp(p)} controls={band && <BandChip inside={band.inside} />}>
      <DialBeside
        dial={
          band ? (
            <BandDial band={band} unit={`${p.benchmark ?? "SPY"} sh`} label="beta-weighted delta" fmt={(v) => signed(v, places)} />
          ) : (
            <p className="muted">no band recorded</p>
          )
        }
      >
        <div className="pf-tiles">
          <Tile label={`per 1% ${p.benchmark ?? "SPY"} move`} value={fmtCash(p.bwdDollarsPer1pct)} />
          <Tile label="band" value={band ? `${signed(band.low, places)} to ${signed(band.high, places)}` : "—"} />
        </div>
        {first && p.hedges ? (
          <p className="pf-hint">
            <span className="chip pf-out">hedge</span>
            <span>
              <strong>
                {p.hedges.root ?? ""} {first.structure} × {first.units}
              </strong>{" "}
              brings it to {signed(first.after?.bwd ?? null)} sh for {fmtMoney(first.maxLoss)} max loss
            </span>
          </p>
        ) : null}
      </DialBeside>
      <BoxButtons>
        <BoxButton id="overTime">over time</BoxButton>
        <BoxButton id="hedge">a hedge, sized</BoxButton>
      </BoxButtons>
    </Card>
  );
}

/**
 * If the market moves: the stress shocks, the break-even move (a tile, with the implied and realised
 * moves on its hover), the implied move, and the risk graph drawn small; the full graph opens from it.
 */
export function StressCard({ p }: { p: ManagePortfolio }) {
  const be = p.breakeven;
  const realised = p.realisedMove !== null && p.benchmarkSpot ? p.realisedMove / p.benchmarkSpot : null;
  const framing = useColumns([
    {
      key: "be",
      node: (
        <Tile
          key="be"
          label="break-even move"
          value={be && be.pct !== null ? pctOf(be.pct) : "—"}
          title={
            be && be.pct !== null
              ? `${be.kind === "short_gamma" ? `a ${pctOf(be.pct)} move in a day wipes out a day's theta` : `the book needs a ${pctOf(be.pct)} move in a day to earn back its theta`} · implied one-day move ${pctOf(p.expectedMovePct)}${realised !== null ? ` · realised (30-day) ${pctOf(realised)}` : ""}`
              : "theta and gamma do not oppose each other, so there is no break-even move"
          }
        />
      ),
    },
    {
      key: "em",
      node: (
        <Tile
          key="em"
          label={`${p.benchmark ?? "SPY"} implied 1-day move`}
          value={p.expectedMove === null ? "—" : `±${p.expectedMove.toFixed(2)}`}
          title="points, from its implied volatility index, per calendar day"
        />
      ),
    },
    // The detail view's pair, so the tiles stay even: four in simple, six in detail.
    {
      key: "realised",
      detailOnly: true,
      node: <Tile key="realised" label="realised move (30-day)" value={pctOf(realised)} title={`${p.benchmark ?? "SPY"}'s realised daily move over 30 days, beside the implied`} />,
    },
    {
      key: "vega",
      detailOnly: true,
      node: <Tile key="vega" label="vega (30-day weighted)" value={fmtCash(p.vega30)} title="dollars per volatility point, front months weighted up" />,
    },
  ]);
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
    ...framing.map((f) => f.node),
  ].slice(0, 6);
  return (
    <Card title="if the market moves" className="pf-cq" help={STRESS_HELP}>
      <div className="pf-stress">
        <div className={`pf-tiles pf-tiles-${tiles.length}`}>{tiles}</div>
        {p.scenarios ? <RiskGraph s={p.scenarios} benchmark={p.benchmark ?? "SPY"} spot={p.benchmarkSpot} small /> : null}
      </div>
      <BoxButtons>
        <BoxButton id="graph">risk graph</BoxButton>
      </BoxButtons>
    </Card>
  );
}

const CONTRIBUTION_ROWS = 5;

/** A figure's contribution by name, biggest first; past `limit` the rest are summed into one row, so
 *  the rows add up to the book's figure. `limit` null lists every name. */
export function ContributionBars({ p, figure, limit = CONTRIBUTION_ROWS }: { p: ManagePortfolio; figure: "bwd" | "theta"; limit?: number | null }) {
  const sorted = [...p.underlyings]
    .filter((u) => u[figure] !== null)
    .sort((a, b) => Math.abs(b[figure] ?? 0) - Math.abs(a[figure] ?? 0));
  const cut = limit === null ? sorted.length : limit;
  const rows = sorted.slice(0, cut).map((u) => ({
    label: u.betaDoubt && figure === "bwd" ? `${u.underlying} ⚠` : u.underlying,
    value: u[figure],
    title: `${u.underlying}: beta ${u.beta === null ? "—" : u.beta.toFixed(2)} · ${pctOf(figure === "bwd" ? u.bwdShare : u.thetaShare, 0)} of the book's gross ${
      figure === "bwd" ? "delta" : "theta without the legs about to expire"
    }${u.betaDoubt ? ` · ${u.betaDoubt}` : ""}`,
  }));
  const rest = sorted.slice(cut);
  if (rest.length > 0) {
    rows.push({
      label: `${rest.length} other${rest.length === 1 ? "" : "s"}`,
      value: rest.reduce((s, u) => s + (u[figure] ?? 0), 0),
      title: rest.map((u) => u.underlying).join(", "),
    });
  }
  return (
    <>
      <DivergingBars rows={rows} tone="none" format={(v) => (figure === "bwd" ? `${signed(v)} sh` : `${fmtCash(v)}/day`)} emptyText="no modelled positions" />
      {figure === "bwd" && p.underlyings.some((u) => u.betaDoubt) && (
        <p className="muted pf-caption">⚠ the broker's beta disagrees with its own correlation for this name, so its delta may be misstated</p>
      )}
    </>
  );
}

/** Where the delta (or theta) comes from: the biggest names, with every name and the broker's model
 *  one click away. The broker button carries the gap, so a large one shows without opening it. */
export function ContributionCard({ p, figure }: { p: ManagePortfolio; figure: "bwd" | "theta" }) {
  const c = p.brokerCheck;
  const gap = c ? (figure === "bwd" ? (c.bwdGap === null ? null : `${signed(c.bwdGap)} sh`) : c.thetaGap === null ? null : `${fmtCash(c.thetaGap)}/day`) : null;
  return (
    <Card title={figure === "bwd" ? "where the delta comes from" : "where the theta comes from"} help={contributionHelp(figure)}>
      <ContributionBars p={p} figure={figure} />
      <BoxButtons>
        <BoxButton id={figure === "bwd" ? "deltaAll" : "thetaAll"}>every name</BoxButton>
        <BoxButton id="broker" hot={figure === "bwd"}>
          against the broker's model{gap ? <span className="pf-more-k"> gap {gap}</span> : null}
        </BoxButton>
      </BoxButtons>
    </Card>
  );
}

/**
 * Our delta and theta against the same sums on the broker's own greeks, over the same legs and betas
 * (`broker_check`). It decides nothing: it is the record of how far our model sits from the broker's,
 * kept before the bands are trusted. Gaps carry no tone, since neither side is the right answer.
 */
export function BrokerCheckBody({ p }: { p: ManagePortfolio }) {
  const c = p.brokerCheck;
  if (!c) return null;
  const sh = (v: number | null) => (v === null ? "—" : `${signed(v)} sh`);
  return (
    <>
      <TileGrid count={6}>
        <Tile label="delta, ours" value={sh(c.bwdOurs)} />
        <Tile label="delta, broker's greeks" value={sh(c.bwdBroker)} />
        <Tile label="delta gap" value={sh(c.bwdGap)} title="ours less the broker's" />
        <Tile label="theta, ours" value={fmtCash(c.thetaOurs)} />
        <Tile label="theta, broker's greeks" value={fmtCash(c.thetaBroker)} />
        <Tile label="theta gap" value={fmtCash(c.thetaGap)} title="ours less the broker's, per day" />
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
        {c.legsOnBrokerIv ? ` (the broker's IV on ${c.legsOnBrokerIv} with a wide quote)` : ""}
        {c.legsLeftOut.length ? ` · left out of both (one model only): ${c.legsLeftOut.join(", ")}` : ""}
      </p>
      {c.expiring && (
        <>
          <div className="card-subhead">expiring today, not in the figures above</div>
          <TileGrid count={6}>
            <Tile label="delta, ours" value={sh(c.expiring.bwdOurs)} />
            <Tile label="delta, broker's greeks" value={sh(c.expiring.bwdBroker)} />
            <Tile label="delta gap" value={sh(c.expiring.bwdGap)} title="ours less the broker's" />
            <Tile label="theta, ours" value={fmtCash(c.expiring.thetaOurs)} />
            <Tile label="theta, broker's greeks" value={fmtCash(c.expiring.thetaBroker)} />
            <Tile label="theta gap" value={fmtCash(c.expiring.thetaGap)} title="ours less the broker's, per day" />
          </TileGrid>
          <p className="muted pf-caption">
            {c.expiring.legs.join(", ")} · ours takes a leg expiring today at its moneyness, its remaining extrinsic as
            the day's theta; the broker's prices a day left at the quote's IV
          </p>
        </>
      )}
    </>
  );
}

/** The broker check as a card, for a page that shows it on its own. */
export function BrokerCheckCard({ p }: { p: ManagePortfolio }) {
  if (!p.brokerCheck) return null;
  return (
    <Card title="against the broker's model">
      <BrokerCheckBody p={p} />
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
  "portfolio.earnings_exposure": "earnings",
  "portfolio.expiry_concentration": "one expiry",
  "portfolio.vol_expansion": "vol expansion",
};

function guidanceTopic(rule: string): string {
  return GUIDANCE_TOPIC[rule] ?? rule.replace(/^portfolio\./, "").replace(/_/g, " ");
}

/** A driver's figure in its own unit: delta in benchmark shares, theta in dollars a day, and a share
 *  of the book as a percentage, named, since a concentration driver carries both shares. */
function driverFigure(f: ManageGuidanceFigure, benchmark: string | null): string {
  switch (f.kind) {
    case "bwd":
      return f.value === null ? "—" : `${signed(f.value)} ${benchmark ? `${benchmark} ` : ""}sh`;
    case "theta":
      return f.value === null ? "—" : `${fmtCash(f.value)}/day`;
    case "bwd_share":
      return `${pctOf(f.value, 0)} of delta`;
    case "theta_share":
      // The band's theta: without the legs about to expire, so it is not the theta card's share.
      return `${pctOf(f.value, 0)} of theta past the expiring legs`;
    case "vega_30":
      return f.value === null ? "—" : `${fmtCash(f.value)}/vol pt`;
  }
}

/** One guidance item as its own small card: the topic as an amber chip (every item is a review), the
 *  rule's confidence, its reason, and what drives it. */
function GuidanceItem({ g, benchmark }: { g: ManageGuidance; benchmark: string | null }) {
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
              <span>{d.figures.map((f) => driverFigure(f, benchmark)).join(" · ") || "—"}</span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

/** The ruleset's own confidence for the portfolio rules, as the caption states it: one figure when
 *  they agree, the range when they do not, and none when the evaluation embeds no ruleset. */
function confidenceText(c: ManagePortfolio["ruleConfidence"]): string {
  if (!c) return "";
  return c.low === c.high ? ` at confidence ${c.low}` : ` at confidence ${c.low}–${c.high}`;
}

export function GuidanceCard({ p }: { p: ManagePortfolio }) {
  return (
    <Card title="portfolio guidance" help={guidanceHelp(p)} flag="no published evidence">
      {p.guidance.length === 0 ? (
        <p className="muted">nothing outside its band today</p>
      ) : (
        <div className="pf-guide-grid">
          {p.guidance.map((g) => (
            <GuidanceItem key={g.rule} g={g} benchmark={p.benchmark} />
          ))}
        </div>
      )}
      {!p.complete && (
        <p className="muted pf-caption">
          partial: {[...p.unmodelledLegs.map((l) => `${l} (not modelled)`), ...p.noBeta.map((u) => `${u} (no beta)`)].join(", ") || "no benchmark price"}
        </p>
      )}
      <BoxButtons>
        <BoxButton id="hedge">a hedge, sized</BoxButton>
      </BoxButtons>
    </Card>
  );
}

/** The sized hedges as trade cards, under the line saying what they are for. */
export function HedgeBody({ p }: { p: ManagePortfolio }) {
  const h = p.hedges;
  if (!h) return null;
  return (
    <>
      <p className="muted pf-caption" style={{ marginTop: 0 }}>
        delta {signed(p.bwd)} sh, needs {signed(h.need)} to reach {signed(h.target)} · {h.root ?? "—"} {h.expiration ?? ""}
        {h.dte !== null ? ` (${h.dte} days)` : ""} · priced at mid, which is not a fill · nothing is placed
      </p>
      {h.error ? (
        <p className="muted">{h.error}</p>
      ) : (
        <>
          {h.suggestions
            .filter((s) => s.error)
            .map((s) => (
              <p key={s.kind} className="muted">
                <strong>{s.structure ?? s.kind}</strong> · {s.error}
              </p>
            ))}
          <TradeCards models={h.suggestions.filter((s) => !s.error).map((s) => hedgeModel(s, h))} />
        </>
      )}
    </>
  );
}

/**
 * Room to act: buying power used against its VIX-scaled cap, with your limits stacked under it, each
 * at its value, its limit and manage's state. The full list and the ruleset open from it.
 */
export function RoomCard({ p, limits }: { p: ManagePortfolio; limits: ManageLimits | null }) {
  const band = p.bands?.buyingPower ?? null;
  const short = (v: number) => `${Math.round(v)}`;
  const rows = (limits?.rows ?? []).filter((r) => r.id !== "buying_power");
  return (
    <Card title="room to act" className="pf-cq" help={ROOM_HELP} controls={band && <BandChip inside={band.inside} />}>
      <DialBeside
        dial={
          band && band.high !== null ? (
            <BandDial band={asPercent(band)} unit="% of net liq used" label="buying power" signedScale={false} fmt={(v) => v.toFixed(0)} tick={short} />
          ) : (
            <p className="muted">no VIX reading, so no cap to set it against</p>
          )
        }
      >
        <ul className="pf-targets">
          <li>
            <span>buying power cap</span>
            <span className="num">
              {band?.high == null ? "—" : `${Math.round(band.high * 100)}%`}
              {band?.vix != null ? ` · VIX ${band.vix.toFixed(1)}` : ""}
            </span>
          </li>
          {rows.map((r) => (
            <li key={r.id}>
              <span>{r.label}</span>
              <span className="num">
                {limitValue(r.value, r.unit)} / {limitText(r)} <span className={limitChip(r.state)}>{r.state}</span>
              </span>
            </li>
          ))}
        </ul>
      </DialBeside>
      <BoxButtons>
        <BoxButton id="overTime">over time</BoxButton>
        <BoxButton id="limits">your limits and ruleset</BoxButton>
      </BoxButtons>
    </Card>
  );
}

/* ---------------------------------------------------------------- each card's help (behind its ?) */

export const INCOME_HELP: CardHelpContent = {
  title: "Reading income",
  body: (
    <>
      <HelpSection title="The dial">
        <p>
          Theta as a share of net liq per day, against the theta band. The band reads theta without the legs about to expire, so a
          leg's last days do not make the book look better paid than it is.
        </p>
      </HelpSection>
      <HelpSection title="The target">
        <p>Theta against the premium pace's target, a share of net liq per day. The months collected and kept are under premium by month.</p>
      </HelpSection>
      <HelpSection title="More is not more edge">
        <p>Theta is paid for by gamma: a book earning more per day is also short more movement.</p>
      </HelpSection>
    </>
  ),
};

/** Direction's help, naming what its band is scaled by today. */
export function directionHelp(p: ManagePortfolio): CardHelpContent {
  const basis = p.bands?.delta.basis ?? null;
  return {
    title: "Reading direction",
    body: (
      <>
        <HelpSection title="The dial">
          <p>
            Beta-weighted delta: each position's delta times its name's beta, in {p.benchmark ?? "SPY"} shares, using the broker's beta.
            Long and short carry no colour, since neither is a verdict.
          </p>
        </HelpSection>
        <HelpSection title="The band">
          <p>
            {basis === "theta"
              ? "Scaled by theta, so a book paid more may lean further."
              : "Scaled by net liq: its floor, used when there is too little theta to scale by."}{" "}
            Amber comes only from manage's own in/out flag, never from where the needle sits.
          </p>
        </HelpSection>
        <HelpSection title="The hedge line">
          <p>While delta is outside its band, manage sizes a hedge; its headline shows here and the cards open under a hedge, sized.</p>
        </HelpSection>
      </>
    ),
  };
}

export const STRESS_HELP: CardHelpContent = {
  title: "Reading if the market moves",
  body: (
    <>
      <HelpSection title="The shocks">
        <p>The whole book repriced with the benchmark down and up, each name moved by its beta, its IV and time held still. A model, not a forecast.</p>
      </HelpSection>
      <HelpSection title="Break-even move">
        <p>The move in a day at which a day's theta is gone (short gamma), or that the book needs to earn its theta back (long gamma). Hover it for the implied and realised moves.</p>
      </HelpSection>
      <HelpSection title="The graph">
        <p>The book's P&L across moves, now and at the nearest expiry. The full graph, with every figure on hover, is under risk graph.</p>
      </HelpSection>
    </>
  ),
};

export function contributionHelp(figure: "bwd" | "theta"): CardHelpContent {
  return {
    title: figure === "bwd" ? "Where the delta comes from" : "Where the theta comes from",
    body: (
      <>
        <HelpSection title="The bars">
          <p>
            {figure === "bwd"
              ? "Each name's beta-weighted delta, biggest first. A ⚠ marks a name whose broker beta disagrees with its own correlation, so its delta may be misstated."
              : "Each name's theta per day, biggest first, without the legs about to expire."}
          </p>
        </HelpSection>
        <HelpSection title="Others">
          <p>The names past the first five are summed into one row, so the rows add up to the book. Every name opens under every name.</p>
        </HelpSection>
        <HelpSection title="Against the broker's model">
          <p>Our figures beside the same sums on the broker's own greeks; its button carries the gap.</p>
        </HelpSection>
      </>
    ),
  };
}

export function guidanceHelp(p: ManagePortfolio): CardHelpContent {
  return {
    title: "Reading the guidance",
    body: (
      <>
        <HelpWarn title="No published evidence">
          <p>Retail heuristics{confidenceText(p.ruleConfidence)}: no published evidence stands behind these bands.</p>
        </HelpWarn>
        <HelpSection title="Each item">
          <p>A portfolio rule the book is outside of today: its reason, the rule's confidence, and the names driving it in each figure's own unit.</p>
        </HelpSection>
      </>
    ),
  };
}

export const ROOM_HELP: CardHelpContent = {
  title: "Reading room to act",
  body: (
    <>
      <HelpSection title="The dial">
        <p>
          Buying power used as a share of net liq, against a cap that rises with VIX, since premium is richer then. Less room means less
          to adjust with when the market moves.
        </p>
      </HelpSection>
      <HelpSection title="Your limits">
        <p>Each measure beside the limit it is judged against, and manage's word on it. The full list and the ruleset open under your limits and ruleset.</p>
      </HelpSection>
    </>
  ),
};
