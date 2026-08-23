import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import type { TradingMode } from "@console/shared";
import { fmtMoney } from "../../components/DataTable";
import { AXIS_MUTED } from "../../components/Charts";

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
  profile: string;
  positions: ForestPosition[];
  prices: number[];
  pnl: number[];
  outcome: { entered: number; stopped: number; expired: number; open: number; realisedNet: number };
  perPosition: Array<{ icOrderId: string; pnl: number[] }>;
}

interface MeicForest {
  mode: TradingMode;
  tradeDate: string | null;
  symbol: string | null;
  tradesToday: number;
  openPositions: number;
  asEntered: ForestArm[];
  arms: ForestArm[];
  releasedStrikes: Array<{ profile: string; strike: number; right: "P" | "C"; at: string | null }>;
  lastSpot: number | null;
}

// No brand accent (#d23f57) here -- reserved for brand/live/alert moments, not "just the first arm".
const ARM_COLORS = ["#7aa2ff", "#43b57a", "#d9a13b", "#a06bd9", "#4fc3d9", "#e88a5c", "#8a9c4a"];

function useMeicForest(mode: TradingMode, date: string | null) {
  return useQuery<MeicForest>({
    queryKey: ["meic-forest", mode, date],
    queryFn: async () => {
      const qs = new URLSearchParams({ mode });
      if (date !== null) qs.set("date", date);
      const res = await fetch(`/api/meic/forest?${qs.toString()}`);
      if (!res.ok) throw new Error(`meic forest: HTTP ${res.status}`);
      return (await res.json()) as MeicForest;
    },
    refetchInterval: 30_000,
  });
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

function path(prices: number[], pnl: number[], X: (v: number) => number, Y: (v: number) => number): string {
  return prices.map((p, i) => `${i === 0 ? "M" : "L"}${X(p).toFixed(1)},${Y(pnl[i] ?? 0).toFixed(1)}`).join(" ");
}

export function MeicForestCard({ mode, date = null }: { mode: TradingMode; date?: string | null }) {
  const { data, isLoading } = useMeicForest(mode, date);
  const [showNested, setShowNested] = useState(true);
  // A MEIC book resolves ENTIRELY at settlement — every trade ends stopped or expired — so after
  // 16:00 there is no open book and an expiry-payoff curve has nothing left to describe. The card
  // was simply blank every evening. Falling back to the day's book AS ENTERED keeps the session's
  // accumulated structure readable, clearly labelled as geometry rather than outcome: a stopped
  // side came off before expiry, and its realized P&L is not this curve.
  const live = (data?.arms.length ?? 0) > 0;
  const arms = live ? (data?.arms ?? []) : (data?.asEntered ?? []);
  const [hover, setHover] = useState<{ price: number } | null>(null);

  const shown = arms.filter((a) => a.positions.length > 0 && a.prices.length > 0);

  const width = 1150;
  const height = 320;
  const pad = { l: 66, r: 12, t: 20, b: 26 };

  // Realised across the arms actually drawn, so the reference line and the legend cannot disagree.
  const realisedNet = shown.length > 0 ? shown.reduce((sum, a) => sum + (a.outcome?.realisedNet ?? 0), 0) : null;
  const stopped = shown.reduce((n, a) => n + (a.outcome?.stopped ?? 0), 0);
  const expired = shown.reduce((n, a) => n + (a.outcome?.expired ?? 0), 0);

  let body = null;
  if (shown.length > 0) {
    const prices = shown[0]!.prices;
    const xMin = Math.min(...shown.map((a) => a.prices[0] ?? 0));
    const xMax = Math.max(...shown.map((a) => a.prices[a.prices.length - 1] ?? 0));
    let yLo = 0;
    let yHi = 0;
    for (const a of shown) {
      for (const v of a.pnl) {
        yLo = Math.min(yLo, v);
        yHi = Math.max(yHi, v);
      }
    }
    const span = yHi - yLo || 1;
    const yMin = yLo - span * 0.1;
    const yMax = yHi + span * 0.1;

    const X = (v: number) => pad.l + ((v - xMin) / (xMax - xMin || 1)) * (width - pad.l - pad.r);
    const Y = (v: number) => height - pad.b - ((v - yMin) / (yMax - yMin || 1)) * (height - pad.t - pad.b);
    const colorOf = (profile: string) =>
      ARM_COLORS[arms.findIndex((a) => a.profile === profile) % ARM_COLORS.length]!;

    const hoverIdx =
      hover !== null
        ? prices.reduce((best, p, i) => (Math.abs(p - hover.price) < Math.abs((prices[best] ?? 0) - hover.price) ? i : best), 0)
        : null;

    body = (
      <svg
        viewBox={`0 0 ${width} ${height}`}
        role="img"
        aria-label="MEIC profit forest"
        style={{ width: "100%", height: "auto", display: "block" }}
        onMouseMove={(e) => {
          const rect = e.currentTarget.getBoundingClientRect();
          const fx = ((e.clientX - rect.left) / rect.width) * width;
          if (fx >= pad.l && fx <= width - pad.r) {
            setHover({ price: xMin + ((fx - pad.l) / (width - pad.l - pad.r)) * (xMax - xMin) });
          } else setHover(null);
        }}
        onMouseLeave={() => setHover(null)}
      >
        {/* What the book ACTUALLY made, against what the curve says it would have made held to
            expiry. Flat because these trades are closed: their outcome no longer depends on where
            the underlying settles, which is the whole point the curve alone cannot make. */}
        {!live && realisedNet !== null && realisedNet >= yMin && realisedNet <= yMax && (
          <g>
            <line
              x1={pad.l}
              x2={width - pad.r}
              y1={Y(realisedNet)}
              y2={Y(realisedNet)}
              stroke="var(--ok, #43b57a)"
              strokeWidth={1.5}
              strokeDasharray="6 4"
            />
            <text x={width - pad.r} y={Y(realisedNet) - 5} fontSize={10} fill="var(--ok, #43b57a)" textAnchor="end">
              actually realised {fmtMoney(realisedNet)}
            </text>
          </g>
        )}
        {ticksFor(yMin, yMax, 5).map((v) => (
          <g key={`y${v}`}>
            <line x1={pad.l} x2={width - pad.r} y1={Y(v)} y2={Y(v)} stroke={v === 0 ? "#3a424e" : "#1e232b"} />
            <text x={pad.l - 6} y={Y(v) + 3} fontSize={10} fill={AXIS_MUTED} textAnchor="end">
              {fmtMoney(v)}
            </text>
          </g>
        ))}
        {ticksFor(xMin, xMax, 8).map((v) => (
          <text key={`x${v}`} x={X(v)} y={height - pad.b + 14} fontSize={10} fill={AXIS_MUTED} textAnchor="middle">
            {v.toFixed(0)}
          </text>
        ))}

        {/* Each IC's own curve, faint, behind the aggregate — the nesting made visible. */}
        {showNested &&
          shown.flatMap((a) =>
            a.perPosition.map((p) => (
              <path
                key={`${a.profile}-${p.icOrderId}`}
                d={path(a.prices, p.pnl, X, Y)}
                fill="none"
                stroke={colorOf(a.profile)}
                strokeWidth={1}
                opacity={0.28}
              />
            )),
          )}

        {shown.map((a) => (
          <path key={a.profile} d={path(a.prices, a.pnl, X, Y)} fill="none" stroke={colorOf(a.profile)} strokeWidth={2} />
        ))}

        {/* Strikes a stop has released: they no longer constrain a new entry, and
            the occupancy map must agree with this chart about that. */}
        {(data?.releasedStrikes ?? []).map((r, i) => (
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
            a mark on the same axis. */}
        {data?.lastSpot != null && data.lastSpot >= xMin && data.lastSpot <= xMax && (
          <g>
            <line
              x1={X(data.lastSpot)}
              x2={X(data.lastSpot)}
              y1={pad.t}
              y2={height - pad.b}
              stroke="#e8c547"
              strokeWidth={1}
              strokeDasharray="4 3"
            />
            <text x={X(data.lastSpot)} y={pad.t - 6} fontSize={10} fill="#e8c547" textAnchor="middle">
              spot {data.lastSpot.toFixed(2)}
            </text>
          </g>
        )}

        {hoverIdx !== null && hover !== null && (
          <line x1={X(hover.price)} x2={X(hover.price)} y1={pad.t} y2={height - pad.b} stroke="#3a424e" strokeDasharray="3 3" />
        )}
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
              <span key={a.profile} className="muted" style={{ fontSize: 11, marginRight: "0.9rem" }}>
                <i
                  style={{
                    background: ARM_COLORS[(data?.arms ?? []).findIndex((x) => x.profile === a.profile) % ARM_COLORS.length],
                    display: "inline-block",
                    width: 8,
                    height: 8,
                    marginRight: 4,
                  }}
                />
                {a.profile} — {a.positions.length} {a.positions.length === 1 ? "condor" : "condors"}
                {!live && a.outcome !== undefined && (
                  <span className={a.outcome.realisedNet >= 0 ? "pnl-pos" : "pnl-neg"}>
                    {" "}· made {fmtMoney(a.outcome.realisedNet)}
                  </span>
                )}
                {hover !== null &&
                  ` · ${fmtMoney(
                    a.pnl[
                      a.prices.reduce(
                        (best, p, idx) => (Math.abs(p - hover.price) < Math.abs((a.prices[best] ?? 0) - hover.price) ? idx : best),
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
                {expired} expired. For a profile that stops, the wings below are the loss its stops existed to prevent
                rather than one it ran; for a profile that holds to expiry they are real. Which is which is in the
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
