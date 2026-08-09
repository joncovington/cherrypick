import { useQuery } from "@tanstack/react-query";
import type { TradingMode } from "@console/shared";
import { useQuote } from "../../lib/useQuote";
import { fmtMoney } from "../../components/DataTable";

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
  arms: Array<{ arm: string; curve: PayoffCurve }>;
}

function useForest(mode: TradingMode) {
  return useQuery<Forest>({
    queryKey: ["flies-forest", mode],
    queryFn: async () => {
      const res = await fetch(`/api/flies/forest?mode=${mode}`);
      if (!res.ok) throw new Error(`forest: HTTP ${res.status}`);
      return (await res.json()) as Forest;
    },
    refetchInterval: 30_000,
  });
}

const ARM_COLORS = ["#d23f57", "#7aa2ff", "#43b57a", "#d9a13b", "#a06bd9", "#4fc3d9", "#e88a5c", "#8a9c4a"];

function floorSentence(arm: string, c: PayoffCurve): string {
  const f = c.floor;
  if (c.empty) return `${arm}: no positions`;
  if (f.floorHolds) return `${arm}: floor holds everywhere — worst ${fmtMoney(f.worst)}`;
  const band = f.band !== null ? ` · profitable ${f.band[0].toFixed(0)}–${f.band[1].toFixed(0)}` : "";
  const tail = f.unboundedBelow ? " · loses beyond the wings" : "";
  return `${arm}: worst ${fmtMoney(f.worst)} at ${f.worstAt?.toFixed(0) ?? "?"}${band}${tail}`;
}

/** The profit forest: one payoff line per arm, live spot marker, per-arm floor sentences. */
export function ForestCard({ mode }: { mode: TradingMode }) {
  const { data, isLoading } = useForest(mode);
  const symbol = "XSP"; // flies trades XSP today; the curves' own price domain governs the axis
  const quote = useQuote(symbol);
  const spot = quote?.last ?? (quote?.bid !== undefined && quote?.ask !== undefined ? (quote.bid + quote.ask) / 2 : null);

  const arms = (data?.arms ?? []).filter((a) => !a.curve.empty);
  const width = 760;
  const height = 300;
  const m = { l: 52, r: 12, t: 10, b: 22 };

  let chart = null;
  if (arms.length > 0) {
    const xs = arms.flatMap((a) => a.curve.prices);
    const ys = arms.flatMap((a) => a.curve.pnl);
    const x0 = Math.min(...xs);
    const x1 = Math.max(...xs);
    const y0 = Math.min(...ys, 0);
    const y1 = Math.max(...ys, 0);
    const sx = (x: number) => m.l + ((x - x0) / Math.max(x1 - x0, 1e-9)) * (width - m.l - m.r);
    const sy = (y: number) => m.t + ((y1 - y) / Math.max(y1 - y0, 1e-9)) * (height - m.t - m.b);
    chart = (
      <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label="profit forest" style={{ width: "100%", height: "auto", display: "block" }}>
        <line x1={m.l} y1={sy(0)} x2={width - m.r} y2={sy(0)} stroke="#23262d" />
        {spot !== null && spot >= x0 && spot <= x1 && (
          <>
            <line x1={sx(spot)} y1={m.t} x2={sx(spot)} y2={height - m.b} stroke="#a6adb8" strokeDasharray="3 3" />
            <text x={sx(spot) + 3} y={m.t + 9} fontSize={10} fill="#a6adb8">spot {spot.toFixed(2)}</text>
          </>
        )}
        {arms.map((a, i) => (
          <polyline
            key={a.arm}
            points={a.curve.prices.map((p, j) => `${sx(p).toFixed(1)},${sy(a.curve.pnl[j]!).toFixed(1)}`).join(" ")}
            fill="none"
            stroke={ARM_COLORS[i % ARM_COLORS.length]}
            strokeWidth={1.5}
          />
        ))}
        {arms.map((a, i) => (
          <text key={a.arm} x={m.l + 4 + i * 88} y={height - 8} fontSize={10} fill={ARM_COLORS[i % ARM_COLORS.length]}>
            {a.arm}
          </text>
        ))}
        <text x={m.l - 4} y={sy(0) + 3} textAnchor="end" fontSize={10} fill="#a6adb8">$0</text>
      </svg>
    );
  }

  return (
    <section className="card">
      <h2>Payoff at expiry — the profit forest{data?.tradeDate !== null && data !== undefined ? ` (${data.tradeDate})` : ""}</h2>
      {isLoading ? (
        <span className="skeleton skeleton-text" style={{ width: "50%" }} />
      ) : arms.length === 0 ? (
        <p className="muted">no positions on the latest day</p>
      ) : (
        <>
          {chart}
          <div style={{ marginTop: "0.5rem" }}>
            {arms.map((a) => (
              <p key={a.arm} className={`muted ${a.curve.floor.floorHolds ? "" : ""}`} style={{ margin: "0.15rem 0", fontSize: 12 }}>
                <span className={a.curve.floor.floorHolds ? "pnl-pos" : "pnl-neg"}>●</span> {floorSentence(a.arm, a.curve)}
              </p>
            ))}
          </div>
        </>
      )}
    </section>
  );
}
