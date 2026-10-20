import type { ManageLadder, ManagePortfolio, ManageScenarios } from "@console/shared";
import { AXIS_FONT, CHART_GRID, CHART_GRID_STRONG, niceTicks } from "../../components/Charts";
import { Card } from "../../components/DataTable";
import { DivergingBars } from "../../components/grid/DivergingBars";
import { fmtCash } from "../../lib/format";
import { BandChip, BandDial, asPercent } from "./PortfolioCards";

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
      <p className="muted pf-caption">
        {band?.vix != null ? `VIX ${band.vix.toFixed(1)} allows up to ${band.high === null ? "—" : `${Math.round(band.high * 100)}%`}` : "VIX unknown"} · the cap
        rises with VIX, since premium is richer · less room means less to adjust with when the market moves
      </p>
    </Card>
  );
}

/* ---------------------------------------------------------------- expiry ladder */

export function LadderCard({ ladder }: { ladder: ManageLadder | null }) {
  if (!ladder) return null;
  const e = ladder.earnings;
  const rows = ladder.expiries
    .filter((x) => x.trades.length > 0 || (x.theta ?? 0) !== 0)
    .map((x) => ({
      label: `${x.expiration} · ${x.trades.length} trade${x.trades.length === 1 ? "" : "s"} · θ ${fmtCash(x.theta)}`,
      value: x.trades.length > 0 ? x.downside : null,
      title: `${x.trades.join(", ") || "no trade's nearest short"} · can still lose ${wholeCash(x.downside)}${
        x.downsideUnknown ? ` (+${x.downsideUnknown} not modelled)` : ""
      } · theta of the legs expiring then ${fmtCash(x.theta)}/day`,
    }));
  return (
    <Card title="what expires when">
      <DivergingBars rows={rows} format={wholeCash} emptyText="no expiries" />
      <p className="muted pf-caption">
        each bar: what the trades whose nearest short expires then can still lose (modelled)
      </p>
      {e.trades.length > 0 && (
        <p className="pf-earnings">
          <span className="chip pf-out">earnings</span> {e.trades.length} trade{e.trades.length === 1 ? "" : "s"} report before
          their short expires: {e.thetaShare === null ? "—" : `${Math.round(e.thetaShare * 100)}%`} of theta, {wholeCash(e.downside)}{" "}
          of downside <span className="muted">· {e.trades.join(", ")}</span>
        </p>
      )}
    </Card>
  );
}

/* ---------------------------------------------------------------- risk graph */

const RG = { w: 1150, h: 260, l: 64, r: 96, t: 14, b: 26 };
const CURVE_COLOR = ["var(--accent)", "var(--chart-special)"];

export function RiskGraph({ s, benchmark, spot }: { s: ManageScenarios; benchmark: string; spot: number | null }) {
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
    <svg viewBox={`0 0 ${RG.w} ${RG.h}`} role="img" aria-label={`${benchmark} risk graph`} className="pf-graph">
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
  const s = p.scenarios;
  if (!s) return null;
  const benchmark = p.benchmark ?? "SPY";
  return (
    <Card title="risk graph">
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
    </Card>
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

export function RiskMatrixCard({ p }: { p: ManagePortfolio }) {
  const s = p.scenarios;
  if (!s) return null;
  const benchmark = p.benchmark ?? "SPY";
  const all = s.matrix.flatMap((r) => r.pnl.filter((v): v is number => v !== null));
  const maxAbs = Math.max(1, ...all.map(Math.abs));
  const shade = (v: number | null) => {
    // A zero (rounded to the dollar) has no sign, so no tone: it is neither a gain nor a loss.
    if (v === null || Math.round(v) === 0) return undefined;
    const alpha = Math.round((0.08 + 0.62 * (Math.abs(v) / maxAbs)) * 100);
    return `color-mix(in srgb, var(${v >= 0 ? "--pos" : "--neg"}) ${alpha}%, transparent)`;
  };
  const rows = [...s.matrix].sort((a, b) => b.ivShift - a.ivShift);
  return (
    <Card title="risk matrix">
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
                <th>IV {r.ivShift === 0 ? "unchanged" : `${r.ivShift > 0 ? "+" : "−"}${Math.round(Math.abs(r.ivShift) * 100)} pts`}</th>
                {r.pnl.map((v, j) => (
                  <td
                    key={j}
                    style={{ background: shade(v) }}
                    className={s.moves[j] === 0 && r.ivShift === 0 ? "pf-matrix-origin" : undefined}
                    title={`${benchmark} ${movePct(s.moves[j]!)}, IV ${r.ivShift === 0 ? "unchanged" : `${r.ivShift > 0 ? "+" : "−"}${Math.round(Math.abs(r.ivShift) * 100)} pts`}: ${wholeCash(v)}${
                      p.netLiq != null && v !== null ? ` (${((v / p.netLiq) * 100).toFixed(1)}% of net liq)` : ""
                    }`}
                  >
                    {wholeCash(v)}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="muted pf-caption">
        modelled P&L, time held still · each name moves by its beta · the IV shift is for a 30-day option
        {s.ivShiftWeighting === "term" ? ", scaled to each leg by √(30 / days left), so front months move most" : ", applied to every leg alike"}
        {" "}· in a selloff IV usually rises, so read the top left
      </p>
    </Card>
  );
}
