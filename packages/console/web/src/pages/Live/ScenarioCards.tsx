import { useState } from "react";
import type { ManageIdea, ManageLadder, ManageMatrixHorizon, ManagePortfolio, ManageScenarios } from "@console/shared";
import { AXIS_FONT, CHART_GRID, CHART_GRID_STRONG, niceTicks } from "../../components/Charts";
import { Card } from "../../components/DataTable";
import { DivergingBars } from "../../components/grid/DivergingBars";
import { fmtCash } from "../../lib/format";
import { HelpSection, HelpWarn, type CardHelpContent } from "../../components/CardHelp";
import { BandChip, BandDial, asPercent } from "./PortfolioCards";
import { dayMonth } from "./TradeCard";
import { BoxButton, BoxButtons, expiryBoxId } from "./portfolioBoxContext";

/**
 * The book's scenarios, drawn: buying power against its VIX-scaled cap, the expiry ladder with the
 * earnings exposure, the risk graph and the risk matrix. Every number is packages/manage's
 * (`portfolio.scenarios`, `portfolio.ladder`, `bands.buying_power`); nothing here reprices or
 * decides. P&L takes its tone from its sign, as everywhere; the cap's in/out is manage's flag.
 */

/** Whole dollars, signed: `-$1,570`, `+$34`. Scenario P&L is modelled; cents would claim precision. */
function wholeCash(v: number | null): string {
  if (v === null) return "—";
  const r = Math.round(v);
  return `${r < 0 ? "-" : r > 0 ? "+" : ""}$${Math.abs(r).toLocaleString("en-US")}`;
}

function movePct(m: number): string {
  if (m === 0) return "0";
  return `${m > 0 ? "+" : "−"}${Math.abs(m * 100).toFixed(Math.abs(m * 100) < 1 ? 1 : 0)}%`;
}

/* ---------------------------------------------------------------- buying power */

export function BuyingPowerCard({ p }: { p: ManagePortfolio }) {
  const band = p.bands?.buyingPower ?? null;
  const short = (v: number) => `${Math.round(v)}`;
  return (
    <Card title="buying power" controls={band && <BandChip inside={band.inside} />}>
      {band && band.high !== null ? (
        <BandDial band={asPercent(band)} unit="% of net liq used" label="buying power" signedScale={false} fmt={(v) => v.toFixed(0)} tick={short} />
      ) : (
        <p className="muted">no VIX reading, so no cap to set it against</p>
      )}
      {band?.basis === "cash" ? (
        <p className="muted pf-caption">
          cash account: up to {band.high === null ? "—" : `${Math.round(band.high * 100)}%`} in use · its holds never grow when the market moves, so the
          rest is kept free to sell after a volatility spike
        </p>
      ) : (
        <p className="muted pf-caption">
          {band?.vix != null ? `VIX ${band.vix.toFixed(1)} allows up to ${band.high === null ? "—" : `${Math.round(band.high * 100)}%`}` : "VIX unknown"} · the
          cap rises with VIX, since premium is richer · less room means less to adjust with when the market moves
        </p>
      )}
    </Card>
  );
}

/* ---------------------------------------------------------------- expiry ladder */

export const LADDER_HELP: CardHelpContent = {
  title: "Reading the expiry ladder",
  body: (
    <>
      <HelpSection title="Each bar">
        <p>What the trades whose nearest short expires that day can still lose, modelled from today's marks.</p>
      </HelpSection>
      <HelpSection title="θ beside each date">
        <p>
          The theta of the legs expiring that day, so a long leg that outlasts its trade's short shows on its own date. A date with only
          such legs says "long legs only"; its downside is counted under the trade's short.
        </p>
      </HelpSection>
      <HelpSection title="Earnings">
        <p>Trades whose name reports before their short expires are named under the bars, with their share of theta and downside.</p>
      </HelpSection>
    </>
  ),
};

export function LadderCard({ ladder }: { ladder: ManageLadder | null }) {
  if (!ladder) return null;
  const e = ladder.earnings;
  const rows = ladder.expiries
    .filter((x) => x.trades.length > 0 || (x.theta ?? 0) !== 0)
    .map((x) => ({
      // A date where only long legs of trades filed under an earlier short expire has no trades of its own.
      label:
        x.trades.length > 0
          ? `${x.expiration} · ${x.trades.length} trade${x.trades.length === 1 ? "" : "s"} · θ ${fmtCash(x.theta)}`
          : `${x.expiration} · long legs only · θ ${fmtCash(x.theta)}`,
      value: x.trades.length > 0 ? x.downside : null,
      title:
        x.trades.length > 0
          ? `${x.trades.join(", ")} · can still lose ${wholeCash(x.downside)}${
              x.downsideUnknown ? ` (+${x.downsideUnknown} not modelled)` : ""
            } · theta of the legs expiring then ${fmtCash(x.theta)}/day`
          : `no trade's nearest short expires then: these are the later legs of trades listed under their short, whose downside is counted there · their theta ${fmtCash(x.theta)}/day`,
    }));
  return (
    <Card title="what expires when" help={LADDER_HELP}>
      <DivergingBars rows={rows} format={wholeCash} emptyText="no expiries" />
      {e.trades.length > 0 && (
        <p className="pf-earnings">
          <span className="chip pf-out">earnings</span> {e.trades.length} trade{e.trades.length === 1 ? "" : "s"} report before
          their short expires: {e.thetaShare === null ? "—" : `${Math.round(e.thetaShare * 100)}%`} of theta, {wholeCash(e.downside)}{" "}
          of downside <span className="muted">· {e.trades.join(", ")}</span>
        </p>
      )}
      {e.byDate.length > 0 && (
        <p className="muted pf-caption">
          by report date:{" "}
          {e.byDate.map((d) => `${d.date} ${wholeCash(d.downside)} (${d.trades.join(", ")})`).join(" · ")}
        </p>
      )}
      {/* Each expiry's trades as position cards, in a lightbox (the portfolio tab's; none elsewhere). */}
      <BoxButtons>
        {ladder.expiries
          .filter((x) => x.trades.length > 0)
          .map((x) => (
            <BoxButton key={x.expiration} id={expiryBoxId(x.expiration)}>
              {dayMonth(x.expiration)}
            </BoxButton>
          ))}
      </BoxButtons>
    </Card>
  );
}

/* ---------------------------------------------------------------- risk graph */

const RG_FULL = { w: 1150, h: 260, l: 64, r: 96, t: 14, b: 26 };
// The card's small copy: a narrower box, so its labels stay legible at a third of the width.
const RG_SMALL = { w: 560, h: 190, l: 58, r: 44, t: 10, b: 24 };
const CURVE_COLOR = ["var(--accent)", "var(--chart-special)"];

export function RiskGraph({ s, benchmark, spot, small = false }: { s: ManageScenarios; benchmark: string; spot: number | null; small?: boolean }) {
  const RG = small ? RG_SMALL : RG_FULL;
  const { moves, curves } = s.graph;
  const ys = curves.flatMap((c) => c.pnl.filter((v): v is number => v !== null));
  if (moves.length < 2 || ys.length === 0) return <p className="muted">no curve to draw</p>;
  const lo0 = Math.min(0, ...ys);
  const hi0 = Math.max(0, ...ys);
  const pad = (hi0 - lo0 || 1) * 0.08;
  const lo = lo0 - pad;
  const hi = hi0 + pad;
  const x0 = moves[0]!;
  const x1 = moves[moves.length - 1]!;
  const X = (m: number) => RG.l + ((m - x0) / (x1 - x0)) * (RG.w - RG.l - RG.r);
  const Y = (v: number) => RG.t + ((hi - v) / (hi - lo)) * (RG.h - RG.t - RG.b);
  const label = (c: { horizonDays: number }) => (c.horizonDays === 0 ? "now" : `at nearest expiry (+${c.horizonDays}d)`);
  const xTicks = moves.filter((m) => Math.abs(Math.round(m * 100) % 5) === 0);
  const step = moves.length > 1 ? moves[1]! - moves[0]! : 0.01;
  return (
    <svg viewBox={`0 0 ${RG.w} ${RG.h}`} role="img" aria-label={`${benchmark} risk graph`} className={small ? "pf-graph pf-graph-small" : "pf-graph"}>
      {niceTicks(lo, hi, 5).map((v) => (
        <g key={v}>
          <line x1={RG.l} y1={Y(v)} x2={RG.w - RG.r} y2={Y(v)} stroke={Math.abs(v) < 1e-9 ? CHART_GRID_STRONG : CHART_GRID} />
          <text x={RG.l - 6} y={Y(v) + 3} textAnchor="end" {...AXIS_FONT}>
            {wholeCash(v)}
          </text>
        </g>
      ))}
      <line x1={X(0)} y1={RG.t} x2={X(0)} y2={RG.h - RG.b} stroke={CHART_GRID_STRONG} strokeDasharray="3 3" />
      {xTicks.map((m) => (
        <text key={m} x={X(m)} y={RG.h - 8} textAnchor="middle" {...AXIS_FONT}>
          {movePct(m)}
        </text>
      ))}
      {curves.map((c, i) => {
        const pts = c.pnl
          .map((v, j) => (v === null ? null : `${X(moves[j]!).toFixed(1)},${Y(v).toFixed(1)}`))
          .filter((x): x is string => x !== null)
          .join(" ");
        const last = c.pnl[c.pnl.length - 1];
        return (
          <g key={c.horizonDays}>
            <polyline points={pts} fill="none" stroke={CURVE_COLOR[i % 2]} strokeWidth={2} strokeLinejoin="round" />
            {last != null && (
              <text x={RG.w - RG.r + 6} y={Y(last) + 3} className="pf-graph-label">
                {c.horizonDays === 0 ? "now" : `+${c.horizonDays}d`}
              </text>
            )}
          </g>
        );
      })}
      {/* The hover layer: one column per move, wider than any mark, naming every curve's figure there. */}
      {moves.map((m, j) => (
        <rect key={m} x={X(m) - ((X(x0 + step) - X(x0)) / 2)} y={RG.t} width={X(x0 + step) - X(x0)} height={RG.h - RG.t - RG.b} fill="transparent">
          <title>
            {`${benchmark} ${movePct(m)}${spot ? ` (${(spot * (1 + m)).toFixed(2)})` : ""}\n${curves
              .map((c) => `${label(c)}: ${wholeCash(c.pnl[j] ?? null)}`)
              .join("\n")}`}
          </title>
        </rect>
      ))}
    </svg>
  );
}

export function RiskGraphCard({ p }: { p: ManagePortfolio }) {
  if (!p.scenarios) return null;
  return (
    <Card title="risk graph">
      <RiskGraphBody p={p} />
    </Card>
  );
}

/** The full risk graph with its legend and caption: the card's, and the portfolio tab's lightbox. */
export function RiskGraphBody({ p }: { p: ManagePortfolio }) {
  const s = p.scenarios;
  if (!s) return null;
  const benchmark = p.benchmark ?? "SPY";
  return (
    <>
      <div className="pf-legend">
        {s.graph.curves.map((c, i) => (
          <span key={c.horizonDays}>
            <span className="pf-swatch" style={{ background: CURVE_COLOR[i % 2] }} />
            {c.horizonDays === 0 ? "now" : `at the nearest expiry, ${c.horizonDays} days on`}
          </span>
        ))}
      </div>
      <RiskGraph s={s} benchmark={benchmark} spot={p.benchmarkSpot} />
      <p className="muted pf-caption">
        the whole book's modelled P&L as {benchmark} moves, each name by its beta · legs expiring by the later curve are worth
        their intrinsic value · hover for figures{s.leftOut.length ? ` · left out (no beta): ${s.leftOut.join(", ")}` : ""}
      </p>
    </>
  );
}

/** The scenario cards: buying power beside the expiry ladder, then the graph and the matrix full width. */
export function ScenarioCards({ p }: { p: ManagePortfolio }) {
  return (
    <>
      <div className="cards cards-pairs">
        <BuyingPowerCard p={p} />
        <LadderCard ladder={p.ladder} />
      </div>
      <RiskGraphCard p={p} />
      <RiskMatrixCard p={p} />
    </>
  );
}

/* ---------------------------------------------------------------- risk matrix */

/** "IV +5 pts", "IV −2.3 pts", "IV unchanged": a shift in vol points for a 30-day option. */
function ivText(shift: number): string {
  if (Math.abs(shift) < 0.0005) return "IV unchanged";
  const pts = Math.round(Math.abs(shift) * 1000) / 10;
  return `IV ${shift > 0 ? "+" : "−"}${Number.isInteger(pts) ? pts.toFixed(0) : pts.toFixed(1)} pts`;
}

const DEFAULT_HORIZON_DAYS = 7;

function horizonLabel(days: number): string {
  return days === 0 ? "now" : `+${days} days`;
}

export function RiskMatrixCard({ p }: { p: ManagePortfolio }) {
  const [chosen, setChosen] = useState<number | null>(null);
  const s = p.scenarios;
  if (!s) return null;
  const benchmark = p.benchmark ?? "SPY";
  // An evaluation from before the horizons carries only the time-held-still matrix.
  const horizons: ManageMatrixHorizon[] =
    s.horizons.length > 0 ? s.horizons : [{ horizonDays: 0, windowDays: null, matrix: s.matrix, realistic: null }];
  // The card opens a week on, the decay in; a ruleset without a 7-day horizon opens on its first.
  const pick = (days: number | null) => (days === null ? -1 : horizons.findIndex((x) => x.horizonDays === days));
  const at = [pick(chosen), pick(DEFAULT_HORIZON_DAYS), 0].find((i) => i >= 0)!;
  const h = horizons[at]!;
  const real = h.realistic;
  const all = [...h.matrix.flatMap((r) => r.pnl), ...(real?.pnl ?? [])].filter((v): v is number => v !== null);
  const maxAbs = Math.max(1, ...all.map(Math.abs));
  const shade = (v: number | null) => {
    // A zero (rounded to the dollar) has no sign, so no tone: it is neither a gain nor a loss.
    if (v === null || Math.round(v) === 0) return undefined;
    const alpha = Math.round((0.08 + 0.62 * (Math.abs(v) / maxAbs)) * 100);
    return `color-mix(in srgb, var(${v >= 0 ? "--pos" : "--neg"}) ${alpha}%, transparent)`;
  };
  const ofNetLiq = (v: number | null) =>
    p.netLiq != null && v !== null ? ` (${((v / p.netLiq) * 100).toFixed(1)}% of net liq)` : "";
  const rows = [...h.matrix].sort((a, b) => b.ivShift - a.ivShift);
  const since = s.realistic?.since?.slice(0, 4) ?? null;
  const realTitle = (j: number) => {
    const m = s.moves[j]!;
    const mult = real?.vixMultiple[j] ?? null;
    const n = real?.windows[j] ?? null;
    const shift = real?.ivShift[j] ?? null;
    const head = `${benchmark} ${movePct(m)}, IV as history moves it`;
    if (m === 0) return `${head}: IV unchanged · ${wholeCash(real?.pnl[j] ?? null)}${ofNetLiq(real?.pnl[j] ?? null)}`;
    if (mult === null || shift === null) return `${head}: too few ${h.windowDays}-day windows with a move this size (${n ?? 0})`;
    return `${head}: over ${n} windows of ${h.windowDays} trading days${since ? ` since ${since}` : ""} with a move this size, VIX ended ×${mult.toFixed(2)} (median), so ${ivText(shift)} · ${wholeCash(real?.pnl[j] ?? null)}${ofNetLiq(real?.pnl[j] ?? null)}`;
  };
  return (
    <Card
      title="risk matrix"
      help={matrixHelp(s, h, since, p.ideas.length > 0)}
      flag={p.ideas.length > 0 ? "ideas: no published evidence" : undefined}
      controls={
        horizons.length > 1 && (
          <span className="mode-toggle pf-horizons" role="group" aria-label="horizon">
            {horizons.map((x, i) => (
              <button
                key={x.horizonDays}
                type="button"
                className={i === at ? "mode-btn active" : "mode-btn"}
                aria-pressed={i === at}
                onClick={() => setChosen(x.horizonDays)}
                title={x.horizonDays === 0 ? "time held still: what the book risks, without the decay it is paid" : `${x.horizonDays} days on, nothing done meanwhile: the decay is in`}
              >
                {horizonLabel(x.horizonDays)}
              </button>
            ))}
          </span>
        )
      }
    >
      <div className="table-scroll">
        <table className="pf-matrix">
          <thead>
            <tr>
              <th>{benchmark} →</th>
              {s.moves.map((m) => (
                <th key={m}>{movePct(m)}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.ivShift}>
                <th>{ivText(r.ivShift)}</th>
                {r.pnl.map((v, j) => (
                  <td
                    key={j}
                    style={{ background: shade(v) }}
                    className={s.moves[j] === 0 && r.ivShift === 0 ? "pf-matrix-origin" : undefined}
                    title={`${benchmark} ${movePct(s.moves[j]!)}, ${ivText(r.ivShift)}: ${wholeCash(v)}${ofNetLiq(v)}`}
                  >
                    {wholeCash(v)}
                  </td>
                ))}
              </tr>
            ))}
            {real && (
              <tr className="pf-matrix-real">
                <th title="IV moved with the market as VIX has moved over past windows like this one">IV as history moves it</th>
                {real.pnl.map((v, j) => (
                  <td key={j} style={{ background: shade(v) }} title={realTitle(j)}>
                    {wholeCash(v)}
                  </td>
                ))}
              </tr>
            )}
          </tbody>
        </table>
      </div>
      {/* What the grid is now (its horizon, and why a history row is missing) stays on the card; how it
          is made is behind the ?. */}
      <p className="muted pf-caption">
        modelled P&L{h.horizonDays === 0 ? ", time held still" : `, ${h.horizonDays} days on with nothing done`}
        {real ? " · hover a history cell for its windows" : s.realistic?.error ? ` · no history row: ${s.realistic.error}` : ""}
      </p>
      <MatrixIdeas ideas={p.ideas} benchmark={benchmark} />
    </Card>
  );
}

/* ---------------------------------------------------------------- ideas under the matrix */

function Change({ before, after, money = wholeCash }: { before: number | null; after: number | null; money?: (v: number | null) => string }) {
  const tone = before === null || after === null || Math.round(after) === Math.round(before) ? "" : after > before ? "pf-better" : "pf-worse";
  return (
    <span>
      {money(before)} → <span className={tone}>{money(after)}</span>
    </span>
  );
}

function IdeaCard({ idea, benchmark }: { idea: ManageIdea; benchmark: string }) {
  const e = idea.effect;
  const ends =
    e && e.moves.length > 1
      ? [0, e.moves.length - 1].map((j) => ({ m: e.moves[j]!, before: e.before[j] ?? null, after: e.after[j] ?? null }))
      : [];
  const theta = (v: number | null) => (v === null ? "—" : `$${Math.round(v).toLocaleString("en-US")}`);
  return (
    <section className="md-card pf-idea" title={idea.trades.join(", ")}>
      <h4 className="pf-idea-head">{idea.header}</h4>
      <p className="pf-idea-sub">{idea.subheader}</p>
      <p className="pf-guide-reason">{idea.explanation}</p>
      {e && (
        <>
          <p className="muted pf-idea-change">
            {e.change}, {e.basis === "realistic" ? "on the history row" : "IV unchanged"}, now:
          </p>
          <ul className="pf-guide-drivers">
            <li>
              <span>worst cell</span>
              <Change before={e.worstBefore} after={e.worstAfter} />
            </li>
            {ends.map((x) => (
              <li key={x.m}>
                <span>
                  {benchmark} {movePct(x.m)}
                </span>
                <Change before={x.before} after={x.after} />
              </li>
            ))}
            <li>
              <span>theta per day</span>
              <Change before={e.thetaBefore} after={e.thetaAfter} money={theta} />
            </li>
          </ul>
        </>
      )}
    </section>
  );
}

export function MatrixIdeas({ ideas, benchmark }: { ideas: ManageIdea[]; benchmark: string }) {
  if (ideas.length === 0) return null;
  return (
    <div className="pf-ideas">
      <h3 className="pf-ideas-title">ideas for the matrix</h3>
      <div className="pf-guide-grid">
        {ideas.map((i) => (
          <IdeaCard key={i.key} idea={i} benchmark={benchmark} />
        ))}
      </div>
      <p className="muted pf-caption">green is better than now, red worse</p>
    </div>
  );
}

/** The matrix's help: how the grid, its history row and the ideas are made. */
export function matrixHelp(s: ManageScenarios, h: ManageMatrixHorizon, since: string | null, ideas: boolean): CardHelpContent {
  return {
    title: "How the matrix is made",
    body: (
      <>
        {ideas ? (
          <HelpWarn title="The ideas have no published evidence">
            <p>
              They are read off the book by fixed thresholds of ours. Each figure reprices today's legs under the change; nothing here
              sizes or places a trade.
            </p>
          </HelpWarn>
        ) : null}
        <HelpSection title="Each cell">
          <p>
            The whole book's modelled P&L at that move of the benchmark and that shift in implied volatility, each name moving by its
            beta.{" "}
            {h.horizonDays === 0
              ? "Time is held still: what the book risks, without the decay it is paid."
              : `${h.horizonDays} days on with nothing done: the decay is in, and legs expiring by then are worth their intrinsic value.`}
          </p>
        </HelpSection>
        <HelpSection title="The IV rows">
          <p>
            The shift is for a 30-day option
            {s.ivShiftWeighting === "term"
              ? ", scaled to each leg by √(30 / days left), so front months move most."
              : ", applied to every leg alike."}{" "}
            In a selloff IV usually rises, so the top left is the corner to read.
          </p>
        </HelpSection>
        <HelpSection title="IV as history moves it">
          <p>
            The last row moves IV as VIX has moved over past {h.windowDays ?? "—"}-day stretches{since ? ` since ${since}` : ""} with a move
            that size: today's VIX times the median ratio. Too few such stretches leaves a cell blank. Hover a cell for its count.
          </p>
        </HelpSection>
      </>
    ),
  };
}
