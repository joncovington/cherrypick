import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import type { TradingMode } from "@console/shared";
import { useQuote } from "../../lib/useQuote";
import { fmtMoney } from "../../components/DataTable";
import { fliesQuery, type FliesFilter } from "../../lib/api";

interface PayoffCurve {
  empty: boolean;
  positions: number;
  prices: number[];
  pnl: number[];
  centers: number[];
  floor: {
    worst: number;
    worstAt: number | null;
    floorHolds: boolean;
    band: [number, number] | null;
    unboundedBelow: boolean;
  };
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

const ARM_COLORS = ["#d23f57", "#7aa2ff", "#43b57a", "#d9a13b", "#a06bd9", "#4fc3d9", "#e88a5c", "#8a9c4a", "#c9628a", "#6bd9c4"];
const SPOT_COLOR = "#d9a13b";

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

function ticksFor(min: number, max: number, target: number): number[] {
  const span = max - min || 1;
  const raw = span / target;
  const mag = Math.pow(10, Math.floor(Math.log10(raw)));
  const step = [1, 2, 5, 10].map((k) => k * mag).find((s) => span / s <= target + 1) ?? 10 * mag;
  const out: number[] = [];
  for (let v = Math.ceil(min / step) * step; v <= max + 1e-9; v += step) out.push(v);
  return out;
}

/** The old page's sentence shape: "gex — worst case -$224.13 at 7745, profitable between 7728 and 7733, and loses outside that band." */
function floorSentence(arm: string, c: PayoffCurve): string {
  const f = c.floor;
  if (c.empty) return `${arm} — no positions`;
  if (f.floorHolds) return `${arm} — floor holds everywhere; worst case ${fmtMoney(f.worst)}`;
  const worst = `worst case ${fmtMoney(f.worst)}${f.worstAt !== null ? ` at ${f.worstAt.toFixed(0)}` : ""}`;
  if (f.band !== null) {
    const outside = f.unboundedBelow ? "loses outside that band" : "bounded outside that band";
    return `${arm} — ${worst}, profitable between ${f.band[0].toFixed(0)} and ${f.band[1].toFixed(0)}, and ${outside}.`;
  }
  return `${arm} — ${worst}; negative everywhere.`;
}

const X_WIDTHS = ["auto", "50", "100", "500", "1000"] as const;
const Y_WIDTHS = ["auto", "250", "500", "1000", "5000"] as const;

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
  const [hover, setHover] = useState<{ px: number; frac: number } | null>(null);
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

  const width = 1150;
  const height = 320;
  const pad = { l: 62, r: 12, t: 20, b: 26 };

  let body = null;
  let legendRow = null;
  if (shown.length > 0) {
    // X window: the day's traded CENTRES, stretched to keep spot (or the
    // settlement print) inside — a spot line the chart can't show is worse
    // than a wider window when price walks away from the structures.
    let cMin = Infinity;
    let cMax = -Infinity;
    for (const a of shown) {
      const anchors = a.curve.centers.length > 0 ? a.curve.centers : a.curve.prices;
      for (const k of anchors) {
        cMin = Math.min(cMin, k);
        cMax = Math.max(cMax, k);
      }
    }
    if (spot !== null) {
      cMin = Math.min(cMin, spot);
      cMax = Math.max(cMax, spot);
    }
    const buffer = Math.max((cMax - cMin) * 0.08, 3);
    const mid = (cMin + cMax) / 2;
    const naturalHalf = (cMax - cMin) / 2 + buffer;
    const half = xwidth === "auto" ? naturalHalf : Math.max(Number(xwidth) / 2, naturalHalf);
    const xMin = mid - half;
    const xMax = mid + half;

    // Y fits the visible range; a fixed tier is a per-side minimum.
    let yLo = 0;
    let yHi = 0;
    for (const a of shown) {
      const r = visibleYRange(a.curve.prices, a.curve.pnl, xMin, xMax);
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
    const colorOf = (arm: string) => ARM_COLORS[allArms.findIndex((a) => a.arm === arm) % ARM_COLORS.length]!;

    const hoverPrice = hover !== null ? xMin + hover.frac * (xMax - xMin) : null;

    body = (
      <svg
        viewBox={`0 0 ${width} ${height}`}
        role="img"
        aria-label="profit forest"
        style={{ width: "100%", height: "auto", display: "block" }}
        onMouseMove={(e) => {
          const rect = e.currentTarget.getBoundingClientRect();
          const fx = ((e.clientX - rect.left) / rect.width) * width;
          if (fx >= pad.l && fx <= width - pad.r) {
            setHover({ px: fx, frac: (fx - pad.l) / (width - pad.l - pad.r) });
          } else setHover(null);
        }}
        onMouseLeave={() => setHover(null)}
      >
        {/* grid + ticks */}
        {ticksFor(yMin, yMax, 5).map((v) => (
          <g key={`y${v}`}>
            <line x1={pad.l} y1={Y(v)} x2={width - pad.r} y2={Y(v)} stroke={Math.abs(v) < 1e-9 ? "#3d4653" : "#15181e"} />
            <text x={4} y={Y(v) + 3} fontSize={9} fill="#82878f" fontFamily="Consolas, monospace">
              {fmtMoney(v)}
            </text>
          </g>
        ))}
        {ticksFor(xMin, xMax, 6).map((v) => (
          <g key={`x${v}`}>
            <line x1={X(v)} y1={pad.t} x2={X(v)} y2={height - pad.b} stroke="#15181e" />
            <text x={X(v)} y={height - 8} fontSize={9} fill="#82878f" textAnchor="middle" fontFamily="Consolas, monospace">
              {v.toFixed(0)}
            </text>
          </g>
        ))}
        <text x={width - pad.r} y={height - 8} fontSize={9} fill="#5c626d" textAnchor="end">
          Strike Price
        </text>
        <text
          x={10}
          y={pad.t + (height - pad.t - pad.b) / 2}
          fontSize={9}
          fill="#5c626d"
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
              return (
                <polyline
                  points={xs.map((x, i) => `${X(x).toFixed(1)},${Y(ys[i]!).toFixed(1)}`).join(" ")}
                  fill="none"
                  stroke={colorOf(a.arm)}
                  strokeWidth={shown.length === 1 ? 1.8 : 1.4}
                />
              );
            })()}
          </g>
        ))}

        {/* spot/settlement line: solid amber with an outlined amber tag at the top */}
        {spot !== null && spot >= xMin && spot <= xMax && (
          <>
            <line x1={X(spot)} y1={pad.t} x2={X(spot)} y2={height - pad.b} stroke={SPOT_COLOR} strokeWidth={2} opacity={0.9} />
            {(() => {
              const label = `${spotTag} ${spot.toFixed(2)}`;
              const lw = label.length * 5.6 + 12;
              const lx = Math.min(Math.max(X(spot) - lw / 2, pad.l), width - pad.r - lw);
              return (
                <>
                  <rect x={lx} y={1} width={lw} height={15} rx={4} fill="#101216" stroke={SPOT_COLOR} strokeWidth={1} />
                  <text x={lx + lw / 2} y={12} fontSize={9.5} fontWeight={700} fill={SPOT_COLOR} textAnchor="middle" fontFamily="Consolas, monospace">
                    {label}
                  </text>
                </>
              );
            })()}
          </>
        )}

        {/* hover crosshair + readout */}
        {hover !== null && hoverPrice !== null && (
          <>
            <line x1={hover.px} y1={pad.t} x2={hover.px} y2={height - pad.b} stroke="#3d4653" />
            {(() => {
              const lines = [
                `at ${hoverPrice.toFixed(0)}`,
                ...shown.map((a) => {
                  let best = 0;
                  a.curve.prices.forEach((p, i) => {
                    if (Math.abs(p - hoverPrice) < Math.abs(a.curve.prices[best]! - hoverPrice)) best = i;
                  });
                  return `${a.arm}  ${fmtMoney(a.curve.pnl[best]!)}`;
                }),
              ];
              const bw = Math.max(...lines.map((l) => l.length)) * 5.8 + 12;
              const bh = lines.length * 12 + 8;
              const bx = Math.min(Math.max(hover.px + 12, 4), width - bw - 4);
              return (
                <>
                  <rect x={bx} y={8} width={bw} height={bh} rx={5} fill="#101216f0" stroke="#2a2f3a" />
                  {lines.map((l, i) => (
                    <text key={i} x={bx + 6} y={20 + i * 12} fontSize={9.5} fill={i === 0 ? "#eceff3" : colorOf(shown[i - 1]!.arm)} fontFamily="Consolas, monospace">
                      {l}
                    </text>
                  ))}
                </>
              );
            })()}
          </>
        )}
      </svg>
    );

    legendRow = (
      <div className="forest-legend">
        {shown.map((a) => (
          <span key={a.arm}>
            <i style={{ background: colorOf(a.arm) }} /> {a.arm}
          </span>
        ))}
        <span>
          <i className="forest-dash" style={{ background: "#82878f" }} /> centres
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
                <span className={a.curve.floor.floorHolds ? "pnl-pos" : "pnl-neg"}>●</span> {floorSentence(a.arm, a.curve)}
              </p>
            ))}
            {settled !== null && (
              <p className="muted" style={{ margin: "0.15rem 0", fontSize: 12 }}>
                Settled at <strong>{settled.price.toFixed(2)}</strong> ({settled.source ?? "unknown"})
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
