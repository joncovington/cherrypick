import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import type { TradingMode } from "@console/shared";
import { useQuote } from "../../lib/useQuote";
import { fmtMoney } from "../../components/DataTable";
import { fliesQuery, type FliesFilter } from "../../lib/api";
import { AXIS_MUTED } from "../../components/Charts";
import { ARM_COLORS, SPOT_COLOR } from "../../components/chart/tokens";
import { niceTicks } from "../../components/chart/scales";
import { SpotMarker, HoverReadout } from "../../components/chart/Tooltip";
import { useHoverX } from "../../components/chart/useHoverX";

interface StructureCurve {
  kind: string;
  side: string;
  center: number;
  stranded: boolean;
  pnl: number[];
}

interface BookFloor {
  worst: number;
  worstAt: number | null;
  worstTail: "below" | "above" | null;
  floorHolds: boolean;
  locked: boolean;
  band: [number, number] | null;
  bandOpen: { below: boolean; above: boolean };
  bands: Array<[number, number]>;
  unboundedBelow: boolean;
}

interface PayoffCurve {
  empty: boolean;
  positions: number;
  prices: number[];
  pnl: number[];
  structures: StructureCurve[];
  centers: number[];
  wing: number;
  floor: BookFloor;
}

interface Forest {
  mode: TradingMode;
  tradeDate: string | null;
  /** The day's traded underlying — drives the live-spot subscription. */
  symbol: string | null;
  arms: Array<{ arm: string; curve: PayoffCurve }>;
  settlement: { price: number; source: string | null } | null;
  lastTickSpot: number | null;
}

function useForest(mode: TradingMode, filter: FliesFilter) {
  return useQuery<Forest>({
    queryKey: ["flies-forest", mode, filter],
    queryFn: async () => {
      const res = await fetch(`/api/flies/forest?${fliesQuery(mode, filter)}`);
      if (!res.ok) throw new Error(`forest: HTTP ${res.status}`);
      return (await res.json()) as Forest;
    },
    refetchInterval: 30_000,
  });
}

/** A payoff is genuinely flat beyond its own scanned range — carry the floor to the window edges. */
function extendFlat(xs: number[], ys: number[], xMin: number, xMax: number): { xs: number[]; ys: number[] } {
  const ex = [...xs];
  const ey = [...ys];
  if (ex[0]! > xMin) {
    ex.unshift(xMin);
    ey.unshift(ey[0]!);
  }
  if (ex[ex.length - 1]! < xMax) {
    ex.push(xMax);
    ey.push(ey[ey.length - 1]!);
  }
  return { xs: ex, ys: ey };
}

/** Min/max payoff actually VISIBLE within the x-window; flat boundary value when outside it. */
function visibleYRange(xs: number[], ys: number[], xMin: number, xMax: number): { min: number; max: number } {
  let mn = Infinity;
  let mx = -Infinity;
  xs.forEach((x, i) => {
    if (x >= xMin && x <= xMax) {
      mn = Math.min(mn, ys[i]!);
      mx = Math.max(mx, ys[i]!);
    }
  });
  if (mn === Infinity) {
    const ext = extendFlat(xs, ys, xMin, xMax);
    mn = mx = ext.ys[0]!;
  }
  return { min: mn, max: mx };
}

/** Nearest grid index to a price. */
function nearestIndex(prices: number[], price: number): number {
  let best = 0;
  prices.forEach((p, i) => {
    if (Math.abs(p - price) < Math.abs(prices[best]! - price)) best = i;
  });
  return best;
}

/**
 * The floor sentence, without the arm name (rendered beside it in the arm's own colour). It says
 * only what the floor actually knows: a band that runs off the scan grid is open on that side,
 * not bounded there; a flat worst case is "at or below" its inner end, not at an arbitrary grid
 * point; and a book whose worst equals its best is locked — nothing price does can change it.
 */
function floorSentence(c: PayoffCurve): string {
  const f = c.floor;
  if (c.empty) return "no positions";
  if (f.locked) return `locked at ${fmtMoney(f.worst)} at every price — nothing price does can change this book`;
  if (f.floorHolds) return `floor holds everywhere; worst case ${fmtMoney(f.worst)}`;
  let worst = `worst case ${fmtMoney(f.worst)}`;
  if (f.worstAt !== null) {
    const at = f.worstAt.toFixed(0);
    worst += f.worstTail === "below" ? ` at or below ${at}` : f.worstTail === "above" ? ` at or above ${at}` : ` at ${at}`;
  }
  if (f.band === null) return `${worst}; negative everywhere.`;
  const [lo, hi] = f.band;
  const where = f.bandOpen.above
    ? `profitable from ${lo.toFixed(0)} upward`
    : f.bandOpen.below
      ? `profitable up to ${hi.toFixed(0)}`
      : `profitable between ${lo.toFixed(0)} and ${hi.toFixed(0)}`;
  const outside = f.unboundedBelow ? "loses outside that band" : "bounded outside that band";
  return `${worst}, ${where}, and ${outside}.`;
}

const X_WIDTHS = ["auto", "50", "100", "500", "1000"] as const;
const Y_WIDTHS = ["auto", "250", "500", "1000", "5000"] as const;

const TENT_COLOR = "#43b57a";
/** Orange, not red: red already means "the book loses here" in the fill. */
const STRANDED_COLOR = "#f0883e";

function structureLabel(s: StructureCurve): string {
  const shape = s.stranded ? `stranded ${s.side} vertical` : s.kind === "fly" ? `${s.side} fly` : s.kind.replace("_", " ");
  return `${shape} ${s.center.toFixed(0)}`;
}

/** Point-for-point equal curves: the advisor's synthetic twin before it diverges from its base. */
function sameCurve(a: PayoffCurve, b: PayoffCurve): boolean {
  return a.prices.length === b.prices.length && a.prices.every((p, i) => p === b.prices[i] && a.pnl[i] === b.pnl[i]);
}

/**
 * The profit forest, matching the flies dashboard's canvas rendering: x-window
 * centred on the books' own centres (width tiers are minimums, spot always
 * kept inside), y fitted to what is visible (range tiers are per-side
 * minimums), curves extended flat to the window edges, single-arm green/red
 * fill, per-arm centre dashlines, labelled spot line, hover readout, legend.
 */
export function ForestCard({ mode, filter }: { mode: TradingMode; filter: FliesFilter }) {
  const { data, isLoading } = useForest(mode, filter);
  const [xwidth, setXwidth] = useState<(typeof X_WIDTHS)[number]>("auto");
  const [ywidth, setYwidth] = useState<(typeof Y_WIDTHS)[number]>("auto");
  const [showParts, setShowParts] = useState(true);
  const width = 1150;
  const height = 320;
  const pad = { l: 62, r: 12, t: 20, b: 26 };
  const { fx: hoverX, onMouseMove: onHoverMove, onMouseLeave: onHoverLeave } = useHoverX(width, pad.l, pad.r);
  // Subscribe the day's actual underlying (SPX days must not show an XSP
  // quote); no symbol on file → no live spot line rather than a wrong-scale one.
  const symbol = data?.symbol ?? null;
  const quote = useQuote(symbol ?? "");
  const liveSpot = quote?.last ?? (quote?.bid !== undefined && quote?.ask !== undefined ? (quote.bid + quote.ask) / 2 : null);

  // A settled day marks the SETTLEMENT print (the number that decided every
  // payoff); only an unsettled current day marks the live spot.
  const settled = data?.settlement ?? null;
  const spot = settled !== null ? settled.price : liveSpot;
  const spotTag = settled !== null ? "settled" : "spot";

  const allArms = data?.arms ?? [];
  const shown = allArms.filter((a) => !a.curve.empty && a.curve.prices.length > 0);
  const colorOf = (arm: string) => ARM_COLORS[allArms.findIndex((a) => a.arm === arm) % ARM_COLORS.length]!;
  // An arm whose curve equals an earlier arm's point for point (an advisor experiment's
  // synthetic book before it diverges) is drawn dashed and named as identical, so a line hidden
  // under another line is never mistaken for a missing one — or for a divergence that has not
  // happened. A synthetic arm is always the twin, never the base: `advised:<experiment>` is
  // identical to its base, not the other way round, whatever order the arms sort in — and with
  // several experiments on one base (2026-09-17) each is named as the base's twin, never as
  // each other's, because twins are only ever matched against arms that are not twins.
  const twinOf = new Map<string, string>();
  const bases = [...shown].sort((a, b) => Number(a.arm.startsWith("advised:")) - Number(b.arm.startsWith("advised:")));
  bases.forEach((a, i) => {
    const base = bases.slice(0, i).find((b) => !twinOf.has(b.arm) && sameCurve(a.curve, b.curve));
    if (base !== undefined) twinOf.set(a.arm, base.arm);
  });
  // Per-structure overlay only on a single arm: the point is to read one book's flat sum back
  // into its tents and its stranded verticals, and several arms' parts on top of each other
  // would say nothing.
  const parts = shown.length === 1 && showParts ? shown[0]!.curve.structures : [];

  let body = null;
  let legendRow = null;
  if (shown.length > 0) {
    // X window: the day's traded CENTRES padded by at least the widest wing in view, so no
    // tent is ever cut mid-slope, and stretched to keep spot (or the settlement print)
    // inside — a spot line the chart can't show is worse than a wider window when price
    // walks away from the structures.
    let cMin = Infinity;
    let cMax = -Infinity;
    let maxWing = 0;
    for (const a of shown) {
      const anchors = a.curve.centers.length > 0 ? a.curve.centers : a.curve.prices;
      for (const k of anchors) {
        cMin = Math.min(cMin, k);
        cMax = Math.max(cMax, k);
      }
      maxWing = Math.max(maxWing, a.curve.wing);
    }
    if (spot !== null) {
      cMin = Math.min(cMin, spot);
      cMax = Math.max(cMax, spot);
    }
    const buffer = Math.max(maxWing, (cMax - cMin) * 0.08, 3);
    const mid = (cMin + cMax) / 2;
    const naturalHalf = (cMax - cMin) / 2 + buffer;
    const half = xwidth === "auto" ? naturalHalf : Math.max(Number(xwidth) / 2, naturalHalf);
    const xMin = mid - half;
    const xMax = mid + half;

    // Y fits the visible range; a fixed tier is a per-side minimum. The structure overlay is
    // part of that range when shown: a locked book is a flat line whose parts are the whole
    // story, and fitting y to the line alone would clip every one of them to nothing.
    let yLo = 0;
    let yHi = 0;
    for (const a of shown) {
      const r = visibleYRange(a.curve.prices, a.curve.pnl, xMin, xMax);
      yLo = Math.min(yLo, r.min);
      yHi = Math.max(yHi, r.max);
    }
    for (const s of parts) {
      const r = visibleYRange(shown[0]!.curve.prices, s.pnl, xMin, xMax);
      yLo = Math.min(yLo, r.min);
      yHi = Math.max(yHi, r.max);
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
    const zero = Y(0);
    const plotTop = pad.t;
    const plotBottom = height - pad.b;

    const hoverPrice = hoverX !== null ? xMin + ((hoverX - pad.l) / (width - pad.l - pad.r)) * (xMax - xMin) : null;

    body = (
      <svg
        viewBox={`0 0 ${width} ${height}`}
        role="img"
        aria-label="profit forest"
        style={{ width: "100%", height: "auto", display: "block" }}
        onMouseMove={onHoverMove}
        onMouseLeave={onHoverLeave}
      >
        <defs>
          <clipPath id="forest-plot">
            <rect x={pad.l} y={plotTop} width={width - pad.l - pad.r} height={plotBottom - plotTop} />
          </clipPath>
        </defs>
        {/* grid + ticks */}
        {niceTicks(yMin, yMax, 5).map((v) => (
          <g key={`y${v}`}>
            <line x1={pad.l} y1={Y(v)} x2={width - pad.r} y2={Y(v)} stroke={Math.abs(v) < 1e-9 ? "#3d4653" : "#15181e"} />
            <text x={4} y={Y(v) + 3} fontSize={9} fill={AXIS_MUTED} fontFamily="Consolas, monospace">
              {fmtMoney(v)}
            </text>
          </g>
        ))}
        {niceTicks(xMin, xMax, 6).map((v) => (
          <g key={`x${v}`}>
            <line x1={X(v)} y1={pad.t} x2={X(v)} y2={height - pad.b} stroke="#15181e" />
            {/* a tick label under the axis title would collide with it — the gridline still shows */}
            {X(v) < width - pad.r - 64 && (
              <text x={X(v)} y={height - 8} fontSize={9} fill={AXIS_MUTED} textAnchor="middle" fontFamily="Consolas, monospace">
                {v.toFixed(0)}
              </text>
            )}
          </g>
        ))}
        <text x={width - pad.r} y={height - 8} fontSize={9} fill={AXIS_MUTED} textAnchor="end">
          Strike Price
        </text>
        <text
          x={10}
          y={pad.t + (height - pad.t - pad.b) / 2}
          fontSize={9}
          fill={AXIS_MUTED}
          textAnchor="middle"
          transform={`rotate(-90 10 ${pad.t + (height - pad.t - pad.b) / 2})`}
        >
          P&L ($)
        </text>

        {/* single-arm green/red fill — the "cannot lose here" claim */}
        {shown.length === 1 &&
          (() => {
            const c = shown[0]!.curve;
            const { xs, ys } = extendFlat(c.prices, c.pnl, xMin, xMax);
            const poly = (sign: 1 | -1, fill: string) => {
              const pts = [
                `${X(xs[0]!).toFixed(1)},${zero.toFixed(1)}`,
                ...xs.map((x, i) => `${X(x).toFixed(1)},${Y(sign > 0 ? Math.max(ys[i]!, 0) : Math.min(ys[i]!, 0)).toFixed(1)}`),
                `${X(xs[xs.length - 1]!).toFixed(1)},${zero.toFixed(1)}`,
              ];
              return <polygon key={sign} points={pts.join(" ")} fill={fill} />;
            };
            return (
              <>
                {poly(1, "rgba(67, 181, 122, 0.28)")}
                {poly(-1, "rgba(217, 92, 74, 0.24)")}
              </>
            );
          })()}

        {/* per-structure overlay: each tent and each stranded vertical on its own, under the book
            sum, clipped to the plot and labelled at its centre so a dashed line can be read back
            to a position without guessing */}
        {parts.length > 0 && (
          <g clipPath="url(#forest-plot)">
            {parts.map((s, i) => {
              const prices = shown[0]!.curve.prices;
              const { xs, ys } = extendFlat(prices, s.pnl, xMin, xMax);
              const color = s.stranded ? STRANDED_COLOR : TENT_COLOR;
              const atCenter = s.pnl[nearestIndex(prices, s.center)]!;
              const labelY = Math.max(plotTop + 9, Math.min(plotBottom - 3, Y(atCenter) - 5));
              return (
                <g key={`part-${i}`}>
                  <polyline
                    points={xs.map((x, j) => `${X(x).toFixed(1)},${Y(ys[j]!).toFixed(1)}`).join(" ")}
                    fill="none"
                    stroke={color}
                    strokeWidth={s.stranded ? 1.3 : 1}
                    strokeDasharray="4 2"
                    opacity={0.85}
                  />
                  {s.center >= xMin && s.center <= xMax && (
                    <text x={X(s.center) + 3} y={labelY} fontSize={8.5} fill={color} opacity={0.9} fontFamily="Consolas, monospace">
                      {structureLabel(s)}
                    </text>
                  )}
                </g>
              );
            })}
          </g>
        )}

        {/* centre dashlines + curves */}
        {shown.map((a) => (
          <g key={a.arm}>
            {a.curve.centers.map((k) =>
              k >= xMin && k <= xMax ? (
                <line key={k} x1={X(k)} y1={pad.t} x2={X(k)} y2={height - pad.b} stroke={colorOf(a.arm)} strokeDasharray="3 3" opacity={0.35} />
              ) : null,
            )}
            {(() => {
              const { xs, ys } = extendFlat(a.curve.prices, a.curve.pnl, xMin, xMax);
              const twin = twinOf.has(a.arm);
              return (
                <polyline
                  points={xs.map((x, i) => `${X(x).toFixed(1)},${Y(ys[i]!).toFixed(1)}`).join(" ")}
                  fill="none"
                  stroke={colorOf(a.arm)}
                  strokeWidth={shown.length === 1 ? 1.8 : 1.4}
                  strokeDasharray={twin ? "6 4" : undefined}
                />
              );
            })()}
          </g>
        ))}

        {/* spot/settlement line: solid amber with an outlined amber tag at the top */}
        {spot !== null && spot >= xMin && spot <= xMax && (
          <SpotMarker x={X(spot)} label={`${spotTag} ${spot.toFixed(2)}`} top={pad.t} bottom={height - pad.b} left={pad.l} right={pad.r} width={width} />
        )}

        {/* hover crosshair + readout */}
        {hoverX !== null && hoverPrice !== null && (
          <>
            <line x1={hoverX} y1={pad.t} x2={hoverX} y2={height - pad.b} stroke="#3d4653" />
            <HoverReadout
              x={hoverX}
              width={width}
              lines={[
                `at ${hoverPrice.toFixed(0)}`,
                ...shown.map((a) => `${a.arm}  ${fmtMoney(a.curve.pnl[nearestIndex(a.curve.prices, hoverPrice)]!)}`),
                ...parts.map((s) => `  ${structureLabel(s)}  ${fmtMoney(s.pnl[nearestIndex(shown[0]!.curve.prices, hoverPrice)]!)}`),
              ]}
              lineColor={(i) => {
                if (i === 0) return "#eceff3";
                if (i <= shown.length) return colorOf(shown[i - 1]!.arm);
                return parts[i - 1 - shown.length]!.stranded ? STRANDED_COLOR : TENT_COLOR;
              }}
            />
          </>
        )}
      </svg>
    );

    legendRow = (
      <div className="forest-legend">
        {shown.map((a) => {
          const twin = twinOf.get(a.arm);
          return (
            <span key={a.arm}>
              <i className={twin !== undefined ? "forest-dash" : undefined} style={twin !== undefined ? { color: colorOf(a.arm) } : { background: colorOf(a.arm) }} />{" "}
              {a.arm}
              {twin !== undefined && <span className="muted"> (identical to {twin})</span>}
            </span>
          );
        })}
        {parts.length > 0 && (
          <>
            <span>
              <i className="forest-dash" style={{ color: TENT_COLOR }} /> completed flies
            </span>
            <span>
              <i className="forest-dash" style={{ color: STRANDED_COLOR }} /> stranded verticals
            </span>
          </>
        )}
        <span>
          <i className="forest-dash" style={{ background: AXIS_MUTED }} /> centres
        </span>
        <span>
          <i style={{ background: SPOT_COLOR }} /> {settled !== null ? "settlement" : "spot now"}
        </span>
      </div>
    );
  }

  return (
    <section className="card">
      <div className="panel-head-row">
        <h2>Payoff at expiry — the profit forest{data?.tradeDate !== null && data !== undefined ? ` (${data.tradeDate})` : ""}</h2>
        <label className="muted lbl">
          x width{" "}
          <select className="text-input" value={xwidth} onChange={(e) => setXwidth(e.target.value as (typeof X_WIDTHS)[number])}>
            {X_WIDTHS.map((w) => (
              <option key={w} value={w}>{w}</option>
            ))}
          </select>
        </label>
        <label className="muted lbl">
          y range{" "}
          <select className="text-input" value={ywidth} onChange={(e) => setYwidth(e.target.value as (typeof Y_WIDTHS)[number])}>
            {Y_WIDTHS.map((w) => (
              <option key={w} value={w}>{w === "auto" ? "auto" : `$${Number(w) >= 1000 ? `${Number(w) / 1000}k` : w}`}</option>
            ))}
          </select>
        </label>
        <label className="muted lbl" title="each fly and each stranded vertical on its own, under the book sum (single arm only)">
          <input type="checkbox" checked={showParts} onChange={(e) => setShowParts(e.target.checked)} /> structures
        </label>
      </div>
      {isLoading ? (
        <span className="skeleton skeleton-text" style={{ width: "50%" }} />
      ) : shown.length === 0 ? (
        <p className="muted">no positions on this day</p>
      ) : (
        <>
          {body}
          {legendRow}
          <div style={{ marginTop: "0.5rem" }}>
            {shown.map((a) => (
              <p key={a.arm} className="muted" style={{ margin: "0.15rem 0", fontSize: 12 }}>
                <span className={a.curve.floor.floorHolds ? "pnl-pos" : "pnl-neg"}>●</span>{" "}
                <span style={{ color: colorOf(a.arm) }}>{a.arm}</span>
                {twinOf.has(a.arm) ? <span className="muted"> (identical to {twinOf.get(a.arm)})</span> : null} — {floorSentence(a.curve)}
              </p>
            ))}
            {settled !== null && (
              <p className="muted" style={{ margin: "0.15rem 0", fontSize: 12 }}>
                Settled at <strong>{settled.price.toFixed(2)}</strong>
                {settled.source !== null ? ` (${settled.source.replace(/_/g, " ")})` : ""}
                {data?.lastTickSpot != null &&
                  ` — last intraday tick was ${data.lastTickSpot.toFixed(2)}, ${Math.abs(data.lastTickSpot - settled.price).toFixed(2)} ${data.lastTickSpot >= settled.price ? "above" : "below"} the close.`}
              </p>
            )}
          </div>
        </>
      )}
    </section>
  );
}
