import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import type { TradingMode } from "@console/shared";
import { fmtMoney } from "../../components/DataTable";
import { AXIS_MUTED } from "../../components/Charts";
import { ARM_COLORS, SPOT_COLOR } from "../../components/chart/tokens";
import { niceTicks } from "../../components/chart/scales";
import { SpotMarker, HoverReadout } from "../../components/chart/Tooltip";
import { useHoverX } from "../../components/chart/useHoverX";
import type { MeicScope } from "./MeicTables";
import { CHART_GRID, CHART_GRID_STRONG } from "../../components/Charts";
import { extendFlat, visibleYRange } from "../../components/chart/payoffWindow";

/**
 * MEIC's profit forest: expiry payoff for each arm's open book.
 *
 * Two things this needs that the flies forest does not.
 *
 * **Nesting is the point.** MEIC stacks condors inside one another deliberately,
 * so each IC's own curve is drawn faintly behind the arm's aggregate. Without
 * that a nested book reads as one lumpy line and the structure that produced it
 * is invisible.
 *
 * **Stops change the shape mid-day.** A stopped side releases its strikes, and
 * those are marked on the axis — otherwise this chart and the strike-occupancy
 * map disagree about what the book still holds, and neither gets trusted.
 *
 * Payoff at EXPIRY, not a mark: nothing here is quoted intraday, and the label
 * says so, the same discipline the flies forest keeps.
 */

interface ForestPosition {
  icOrderId: string;
  putStrike: number;
  callStrike: number;
  wingWidth: number;
  netCredit: number;
  quantity: number;
}

interface ForestArm {
  arm: string;
  positions: ForestPosition[];
  prices: number[];
  pnl: number[];
  outcome: { entered: number; stopped: number; expired: number; open: number; realisedNet: number };
  perPosition: Array<{ icOrderId: string; pnl: number[] }>;
}

interface MeicForest {
  mode: TradingMode;
  tradeDate: string | null;
  /** The one symbol drawn, and every symbol the day traded (the switch appears for two or more). */
  symbol: string | null;
  symbols?: string[];
  tradesToday: number;
  openPositions: number;
  asEntered: ForestArm[];
  arms: ForestArm[];
  releasedStrikes: Array<{ arm: string; strike: number; right: "P" | "C"; at: string | null }>;
  lastSpot: number | null;
}

/**
 * The forest's query string: `date` is the session; symbol/profile/era are the page-wide scope, so
 * the header's controls narrow the forest the way they already narrow the session and history
 * slides. Exported so the wiring is pinned without a DOM.
 */
export function meicForestQuery(mode: TradingMode, scope: MeicScope, forestSymbol: string | null = null): string {
  const qs = new URLSearchParams({ mode });
  if (forestSymbol !== null && scope.symbol === null) qs.set("fsym", forestSymbol);
  if (scope.day !== null) qs.set("date", scope.day);
  if (scope.symbol !== null) qs.set("symbol", scope.symbol);
  if (scope.profile !== null) qs.set("profile", scope.profile);
  if (scope.era !== null) qs.set("era", scope.era);
  return qs.toString();
}

function useMeicForest(mode: TradingMode, scope: MeicScope, forestSymbol: string | null) {
  return useQuery<MeicForest>({
    queryKey: ["meic-forest", mode, scope, forestSymbol],
    queryFn: async () => {
      const res = await fetch(`/api/meic/forest?${meicForestQuery(mode, scope, forestSymbol)}`);
      if (!res.ok) throw new Error(`meic forest: HTTP ${res.status}`);
      return (await res.json()) as MeicForest;
    },
    refetchInterval: 30_000,
  });
}

/** Condor lines the hover readout lists before it says "+N more": a paper day can stack dozens. */
const HOVER_CONDOR_CAP = 10;

/** A payoff path over the window: extended flat to its edges (an IC is flat beyond its wings);
 *  the plot's clip trims whatever a narrower window leaves outside. */
function path(prices: number[], pnl: number[], X: (v: number) => number, Y: (v: number) => number, xMin: number, xMax: number): string {
  const { xs, ys } = extendFlat(prices, pnl, xMin, xMax);
  return xs.map((p, i) => `${i === 0 ? "M" : "L"}${X(p).toFixed(1)},${Y(ys[i] ?? 0).toFixed(1)}`).join(" ");
}

/** Fixed x widths, in SPX points; scaled by the symbol (XSP is a tenth of SPX). */
const X_WIDTHS = ["auto", "50", "100", "200", "500"] as const;
const Y_WIDTHS = ["auto", "250", "500", "1000", "5000"] as const;

export function MeicForestCard({ mode, scope }: { mode: TradingMode; scope: MeicScope }) {
  // The card's own symbol pick (only when the page scope names none): one forest is one price axis.
  const [forestSymbol, setForestSymbol] = useState<string | null>(null);
  const { data, isLoading } = useMeicForest(mode, scope, forestSymbol);
  const [showNested, setShowNested] = useState(true);
  // "per condor" divides each arm's curve by its condor count, so a 6-condor arm and a 332-condor
  // arm read on one axis; "book" is each arm's whole book, the honest total.
  const [perCondor, setPerCondor] = useState(false);
  const [xwidth, setXwidth] = useState<(typeof X_WIDTHS)[number]>("auto");
  const [ywidth, setYwidth] = useState<(typeof Y_WIDTHS)[number]>("auto");
  // XSP is a tenth of SPX, so a fixed width and the buffer floor scale with it.
  const symbolScale = data?.symbol === "XSP" ? 0.1 : 1;
  // A MEIC book resolves ENTIRELY at settlement — every trade ends stopped or expired — so after
  // 16:00 there is no open book and an expiry-payoff curve has nothing left to describe. The card
  // was simply blank every evening. Falling back to the day's book AS ENTERED keeps the session's
  // accumulated structure readable, clearly labelled as geometry rather than outcome: a stopped
  // side came off before expiry, and its realized P&L is not this curve.
  const live = (data?.arms.length ?? 0) > 0;
  const arms = live ? (data?.arms ?? []) : (data?.asEntered ?? []);

  const drawn = arms.filter((a) => a.positions.length > 0 && a.prices.length > 0);
  const shown = perCondor ? drawn.map((a) => ({ ...a, pnl: a.pnl.map((v) => v / a.positions.length) })) : drawn;

  const width = 1150;
  const height = 320;
  const pad = { l: 66, r: 12, t: 20, b: 26 };
  const { fx: hoverX, onMouseMove: onHoverMove, onMouseLeave: onHoverLeave } = useHoverX(width, pad.l, pad.r);

  // Realised across the arms actually drawn, so the reference line and the legend cannot disagree.
  const realisedNet = shown.length > 0 ? shown.reduce((sum, a) => sum + (a.outcome?.realisedNet ?? 0), 0) : null;
  const stopped = shown.reduce((n, a) => n + (a.outcome?.stopped ?? 0), 0);
  const expired = shown.reduce((n, a) => n + (a.outcome?.expired ?? 0), 0);

  let body = null;
  let hoverPrice: number | null = null;
  if (shown.length > 0) {
    const prices = shown[0]!.prices;
    // X: "auto" fits the condors actually drawn (every long wing) plus spot, with a buffer of a
    // wing or 8% of the span; a fixed width is a MINIMUM, centred the same way, so a narrow book
    // can be seen in context but nothing drawn is ever cut off. The flies forest's rule.
    let cMin = Infinity;
    let cMax = -Infinity;
    let maxWing = 0;
    for (const a of shown) {
      for (const p of a.positions) {
        cMin = Math.min(cMin, p.putStrike - p.wingWidth);
        cMax = Math.max(cMax, p.callStrike + p.wingWidth);
        maxWing = Math.max(maxWing, p.wingWidth);
      }
    }
    const spot = data?.lastSpot ?? null;
    if (spot !== null) {
      cMin = Math.min(cMin, spot);
      cMax = Math.max(cMax, spot);
    }
    const buffer = Math.max(maxWing, (cMax - cMin) * 0.08, symbolScale * 3);
    const mid = (cMin + cMax) / 2;
    const naturalHalf = (cMax - cMin) / 2 + buffer;
    const half = xwidth === "auto" ? naturalHalf : Math.max((Number(xwidth) * symbolScale) / 2, naturalHalf);
    const xMin = mid - half;
    const xMax = mid + half;
    // Y fits what is VISIBLE inside the window; a fixed tier is a per-side minimum.
    let yLo = 0;
    let yHi = 0;
    const fit = (pnl: number[]) => {
      const r = visibleYRange(prices, pnl, xMin, xMax);
      yLo = Math.min(yLo, r.min);
      yHi = Math.max(yHi, r.max);
    };
    for (const a of shown) {
      fit(a.pnl);
      if (showNested) for (const p of a.perPosition) fit(p.pnl);
    }
    if (ywidth !== "auto") {
      yLo = Math.min(yLo, -Number(ywidth));
      yHi = Math.max(yHi, Number(ywidth));
    }
    const span = yHi - yLo || 1;
    const yMin = yLo - span * 0.1;
    const yMax = yHi + span * 0.1;

    const X = (v: number) => pad.l + ((v - xMin) / (xMax - xMin || 1)) * (width - pad.l - pad.r);
    const Y = (v: number) => height - pad.b - ((v - yMin) / (yMax - yMin || 1)) * (height - pad.t - pad.b);
    const colorOf = (arm: string) =>
      ARM_COLORS[arms.findIndex((a) => a.arm === arm) % ARM_COLORS.length]!;

    const thisHoverPrice = hoverX !== null ? xMin + ((hoverX - pad.l) / (width - pad.l - pad.r)) * (xMax - xMin) : null;
    hoverPrice = thisHoverPrice;
    const hoverIdx =
      thisHoverPrice !== null
        ? prices.reduce((best, p, i) => (Math.abs(p - thisHoverPrice) < Math.abs((prices[best] ?? 0) - thisHoverPrice) ? i : best), 0)
        : null;

    body = (
      <svg
        viewBox={`0 0 ${width} ${height}`}
        role="img"
        aria-label="MEIC profit forest"
        style={{ width: "100%", height: "auto", display: "block" }}
        onMouseMove={onHoverMove}
        onMouseLeave={onHoverLeave}
      >
        {/* What the book ACTUALLY made, against what the curve says it would have made held to
            expiry. Flat because these trades are closed: their outcome no longer depends on where
            the underlying settles, which is the whole point the curve alone cannot make. */}
        {!live && !perCondor && realisedNet !== null && realisedNet >= yMin && realisedNet <= yMax && (
          <g>
            <line
              x1={pad.l}
              x2={width - pad.r}
              y1={Y(realisedNet)}
              y2={Y(realisedNet)}
              stroke="var(--pos)"
              strokeWidth={1.5}
              strokeDasharray="6 4"
            />
            <text x={width - pad.r} y={Y(realisedNet) - 5} fontSize={10} fill="var(--pos)" textAnchor="end">
              actually realised {fmtMoney(realisedNet)}
            </text>
          </g>
        )}
        {niceTicks(yMin, yMax, 5).map((v) => (
          <g key={`y${v}`}>
            <line x1={pad.l} x2={width - pad.r} y1={Y(v)} y2={Y(v)} stroke={v === 0 ? CHART_GRID_STRONG : CHART_GRID} />
            <text x={pad.l - 6} y={Y(v) + 3} fontSize={10} fill={AXIS_MUTED} textAnchor="end">
              {fmtMoney(v)}
            </text>
          </g>
        ))}
        {niceTicks(xMin, xMax, 8).map((v) => (
          <text key={`x${v}`} x={X(v)} y={height - pad.b + 14} fontSize={10} fill={AXIS_MUTED} textAnchor="middle">
            {v.toFixed(0)}
          </text>
        ))}

        {/* The curves are clipped to the plot: a fixed width narrower than the book's own span
            leaves the outer wings outside the window rather than over the axis labels. */}
        <defs>
          <clipPath id="meic-forest-plot">
            <rect x={pad.l} y={pad.t} width={width - pad.l - pad.r} height={height - pad.t - pad.b} />
          </clipPath>
        </defs>
        <g clipPath="url(#meic-forest-plot)">
        {/* Each IC's own curve, faint, behind the aggregate — the nesting made visible. */}
        {showNested &&
          shown.flatMap((a) =>
            a.perPosition.map((p) => (
              <path
                key={`${a.arm}-${p.icOrderId}`}
                d={path(a.prices, p.pnl, X, Y, xMin, xMax)}
                fill="none"
                stroke={colorOf(a.arm)}
                strokeWidth={1}
                opacity={0.28}
              />
            )),
          )}

        {shown.map((a) => (
          <path key={a.arm} d={path(a.prices, a.pnl, X, Y, xMin, xMax)} fill="none" stroke={colorOf(a.arm)} strokeWidth={2} />
        ))}
        </g>

        {/* Strikes a stop has released: they no longer constrain a new entry, and
            the occupancy map must agree with this chart about that. */}
        {(data?.releasedStrikes ?? []).filter((r) => r.strike >= xMin && r.strike <= xMax).map((r, i) => (
          <line
            key={`rel-${i}`}
            x1={X(r.strike)}
            x2={X(r.strike)}
            y1={height - pad.b - 6}
            y2={height - pad.b}
            stroke={AXIS_MUTED}
            strokeWidth={1}
            strokeDasharray="2 2"
          />
        ))}

        {/* Where the underlying actually sits, against the price axis this curve is drawn over —
            the payoff is at expiry, but "how far are we from that strike right now" still needs
            a mark on the same axis. Same treatment as the flies forest's spot/settlement marker:
            solid amber line, outlined amber tag. */}
        {data?.lastSpot != null && data.lastSpot >= xMin && data.lastSpot <= xMax && (
          <SpotMarker
            x={X(data.lastSpot)}
            label={`spot ${data.lastSpot.toFixed(2)}`}
            top={pad.t}
            bottom={height - pad.b}
            left={pad.l}
            right={pad.r}
            width={width}
          />
        )}

        {/* Hover line + readout, as the flies forest: the price, each arm's payoff there, and each
            condor's while the nested curves are on (capped). */}
        {hoverIdx !== null && hoverPrice !== null && (() => {
          const at = prices[hoverIdx]!;
          const condors = showNested
            ? shown.flatMap((a) =>
                a.perPosition.map((pp, k) => {
                  const pos = a.positions[k]!;
                  return {
                    arm: a.arm,
                    label: `  ${pos.putStrike - pos.wingWidth}/${pos.putStrike}p ${pos.callStrike}/${pos.callStrike + pos.wingWidth}c`,
                    pnl: pp.pnl[hoverIdx] ?? 0,
                  };
                }),
              )
            : [];
          const listed = condors.slice(0, HOVER_CONDOR_CAP);
          const lines = [
            `at ${at.toFixed(0)}`,
            ...shown.map((a) => `${a.arm}  ${fmtMoney(a.pnl[hoverIdx] ?? 0)}`),
            ...listed.map((c) => `${c.label}  ${fmtMoney(c.pnl)}`),
            ...(condors.length > listed.length ? [`  +${condors.length - listed.length} more`] : []),
          ];
          return (
            <>
              <line x1={X(at)} x2={X(at)} y1={pad.t} y2={height - pad.b} stroke={CHART_GRID_STRONG} strokeDasharray="3 3" />
              <HoverReadout
                x={X(at)}
                width={width}
                lines={lines}
                lineColor={(i) => {
                  if (i === 0) return "var(--text)";
                  if (i <= shown.length) return colorOf(shown[i - 1]!.arm);
                  const c = listed[i - 1 - shown.length];
                  return c ? colorOf(c.arm) : AXIS_MUTED;
                }}
              />
            </>
          );
        })()}
      </svg>
    );
  }

  return (
    <section className="card">
      <div className="panel-head-row">
        <h2>
          Payoff at expiry — the profit forest{data?.tradeDate != null ? ` (${data.tradeDate})` : ""}
          {!live && shown.length > 0 && (
            <span className="chain-badge chain-badge-short" style={{ marginLeft: "0.5rem" }} title="No position is open: this book resolved at settlement. Shown as ENTERED — the structure the session accumulated, not what any trade realized. A stopped side came off before expiry and its P&L is not this curve.">
              as entered
            </span>
          )}
        </h2>
        <label className="muted lbl" title={`a minimum window in ${data?.symbol === "XSP" ? "XSP" : "SPX"} points; auto fits the condors drawn and spot`}>
          x width{" "}
          <select className="text-input" value={xwidth} onChange={(e) => setXwidth(e.target.value as (typeof X_WIDTHS)[number])}>
            {X_WIDTHS.map((w) => (
              <option key={w} value={w}>
                {w === "auto" ? "auto" : String(Number(w) * symbolScale)}
              </option>
            ))}
          </select>
        </label>
        {(data?.symbols?.length ?? 0) > 1 && scope.symbol === null && (
          <div className="mode-toggle" role="group" aria-label="forest symbol" title="one symbol per forest: an expiry payoff is drawn on that symbol's own price axis">
            {(data?.symbols ?? []).map((sym) => (
              <button
                key={sym}
                type="button"
                className={`mode-btn ${data?.symbol === sym ? "active" : ""}`}
                onClick={() => setForestSymbol(sym)}
              >
                {sym}
              </button>
            ))}
          </div>
        )}
        <label className="muted lbl" title="book: each arm's whole book; per condor: each arm's curve divided by its condor count, so arms of very different sizes read on one axis">
          y scale{" "}
          <select className="text-input" value={perCondor ? "condor" : "book"} onChange={(e) => setPerCondor(e.target.value === "condor")}>
            <option value="book">book total</option>
            <option value="condor">per condor</option>
          </select>
        </label>
        <label className="muted lbl" title="a minimum y range per side; auto fits the visible curves">
          y range{" "}
          <select className="text-input" value={ywidth} onChange={(e) => setYwidth(e.target.value as (typeof Y_WIDTHS)[number])}>
            {Y_WIDTHS.map((w) => (
              <option key={w} value={w}>
                {w === "auto" ? "auto" : `$${Number(w) >= 1000 ? `${Number(w) / 1000}k` : w}`}
              </option>
            ))}
          </select>
        </label>
        <label className="muted lbl">
          <input type="checkbox" checked={showNested} onChange={(e) => setShowNested(e.target.checked)} /> show each
          condor
        </label>
      </div>
      {isLoading ? (
        <span className="skeleton skeleton-text" style={{ width: "50%" }} />
      ) : shown.length === 0 ? (
        <p className="muted">
          {(data?.tradesToday ?? 0) === 0
            ? "no positions on this day"
            : `${data?.tradesToday} trades on this day, none still open and none reconstructable — nothing to draw`}
        </p>
      ) : (
        <>
          {body}
          <div className="legend-row" style={{ marginTop: "0.4rem" }}>
            {shown.map((a, i) => (
              <span key={a.arm} className="muted" style={{ fontSize: 11, marginRight: "0.9rem" }}>
                <i
                  style={{
                    background: ARM_COLORS[(data?.arms ?? []).findIndex((x) => x.arm === a.arm) % ARM_COLORS.length],
                    display: "inline-block",
                    width: 8,
                    height: 8,
                    marginRight: 4,
                  }}
                />
                {a.arm} — {a.positions.length} {a.positions.length === 1 ? "condor" : "condors"}
                {!live && a.outcome !== undefined && (
                  <span className={a.outcome.realisedNet >= 0 ? "pnl-pos" : "pnl-neg"}>
                    {" "}· made {fmtMoney(a.outcome.realisedNet)}
                  </span>
                )}
                {hoverPrice !== null &&
                  ` · ${fmtMoney(
                    a.pnl[
                      a.prices.reduce(
                        (best, p, idx) => (Math.abs(p - hoverPrice!) < Math.abs((a.prices[best] ?? 0) - hoverPrice!) ? idx : best),
                        0,
                      )
                    ] ?? 0,
                  )}`}
                {i === shown.length - 1 ? "" : ""}
              </span>
            ))}
          </div>
          <p className="muted" style={{ fontSize: 12, margin: "0.4rem 0 0" }}>
            Expiry payoff, not an intraday mark — nothing here is quoted live. Faint lines are the individual condors
            behind each arm's aggregate; dashed ticks on the axis are strikes a stop has released back for re-entry.
            {!live && (
              <>
                {" "}
                <strong>No position is open</strong> — a MEIC book resolves entirely at settlement, so this is the
                day's {data?.tradesToday} trades drawn <strong>as entered</strong>: every one priced as if it had been
                held to expiry.{" "}
                {stopped > 0 && (
                  <>
                    {stopped} were <strong>stopped</strong> and never reached it,{" "}
                  </>
                )}
                {expired} expired. For an arm that stops, the wings below are the loss its stops existed to prevent
                rather than one it ran; for an arm that holds to expiry they are real. Which is which is in the
                legend — each arm's realised figure sits beside it, and the dashed line is the total
                {realisedNet !== null && <> at {fmtMoney(realisedNet)}</>}.
              </>
            )}
          </p>
        </>
      )}
    </section>
  );
}
