import { useState } from "react";

interface Props {
  curve: Array<{ spot: number; pnl: number }>;
  slopes: { below: number; above: number };
  breakevens: number[];
  spot: number | null;
  width?: number;
  height?: number;
}

function fmtMoney0(v: number): string {
  return `${v < 0 ? "-" : ""}$${Math.abs(v).toFixed(0)}`;
}

/** P/L at an arbitrary spot, by linear interpolation along the already-piecewise-linear curve
    (kinks only at strikes, so a straight-line interpolation between the surrounding points is
    exact, not an approximation). */
function pnlAt(points: Array<{ x: number; y: number }>, x: number): number {
  for (let i = 1; i < points.length; i += 1) {
    const a = points[i - 1]!;
    const b = points[i]!;
    if (x >= a.x && x <= b.x) {
      const t = b.x === a.x ? 0 : (x - a.x) / (b.x - a.x);
      return a.y + t * (b.y - a.y);
    }
  }
  return points[points.length - 1]!.y;
}

/**
 * Exact payoff diagram: the curve is piecewise linear with kinks only at
 * strikes, so we render the true polyline (tails extrapolated with the
 * analytic slopes) — profit region tinted green, loss red, via clip paths
 * split at the zero line.
 */
export function PayoffChart({ curve, slopes, breakevens, spot, width = 720, height = 280 }: Props) {
  const [hoverX, setHoverX] = useState<number | null>(null);
  if (curve.length === 0) return <p className="muted">add legs with strikes to see the payoff</p>;

  const first = curve[0]!;
  const last = curve[curve.length - 1]!;
  const strikeSpan = Math.max(last.spot - first.spot, first.spot * 0.05, 1);
  const pad = strikeSpan * 0.35;
  const x0 = first.spot - pad;
  const x1 = last.spot + pad;

  const points: Array<{ x: number; y: number }> = [
    { x: x0, y: first.pnl - slopes.below * (first.spot - x0) },
    ...curve.map((p) => ({ x: p.spot, y: p.pnl })),
    { x: x1, y: last.pnl + slopes.above * (x1 - last.spot) },
  ];

  const ys = points.map((p) => p.y);
  const yMax = Math.max(...ys, 0);
  const yMin = Math.min(...ys, 0);
  const ySpan = Math.max(yMax - yMin, 1);
  const m = { l: 52, r: 8, t: 10, b: 22 };

  const sx = (x: number) => m.l + ((x - x0) / (x1 - x0)) * (width - m.l - m.r);
  const sy = (y: number) => m.t + ((yMax - y) / ySpan) * (height - m.t - m.b);
  const xOf = (px: number) => x0 + ((px - m.l) / (width - m.l - m.r)) * (x1 - x0);

  const line = points.map((p) => `${sx(p.x).toFixed(1)},${sy(p.y).toFixed(1)}`).join(" ");
  const zeroY = sy(0);
  const fill = `${sx(points[0]!.x).toFixed(1)},${zeroY.toFixed(1)} ${line} ${sx(points[points.length - 1]!.x).toFixed(1)},${zeroY.toFixed(1)}`;

  const hoverSpot = hoverX !== null ? xOf(hoverX) : null;
  const hoverPnl = hoverSpot !== null ? pnlAt(points, hoverSpot) : null;

  return (
    <svg
      viewBox={`0 0 ${width} ${height}`}
      role="img"
      aria-label="payoff diagram"
      style={{ width: "100%", height: "auto", display: "block", cursor: "crosshair" }}
      onMouseMove={(e) => {
        const rect = e.currentTarget.getBoundingClientRect();
        const px = ((e.clientX - rect.left) / rect.width) * width;
        setHoverX(Math.min(Math.max(px, m.l), width - m.r));
      }}
      onMouseLeave={() => setHoverX(null)}
    >
      <defs>
        <clipPath id="above-zero">
          <rect x={0} y={0} width={width} height={zeroY} />
        </clipPath>
        <clipPath id="below-zero">
          <rect x={0} y={zeroY} width={width} height={height - zeroY} />
        </clipPath>
      </defs>

      <polygon points={fill} fill="#43b57a" opacity={0.14} clipPath="url(#above-zero)" />
      <polygon points={fill} fill="#d95c4a" opacity={0.14} clipPath="url(#below-zero)" />

      <line x1={m.l} y1={zeroY} x2={width - m.r} y2={zeroY} stroke="#23262d" strokeWidth={1} />
      {spot !== null && spot >= x0 && spot <= x1 && (
        <line
          x1={sx(spot)}
          y1={m.t}
          x2={sx(spot)}
          y2={height - m.b}
          stroke="#a6adb8"
          strokeWidth={1}
          strokeDasharray="3 3"
        />
      )}

      <polyline points={line} fill="none" stroke="#d23f57" strokeWidth={1.8} />

      {curve.map((p) => (
        <circle key={p.spot} cx={sx(p.spot)} cy={sy(p.pnl)} r={2.6} fill="#eceff3" />
      ))}
      {breakevens
        .filter((b) => b >= x0 && b <= x1)
        .map((b) => (
          <circle key={b} cx={sx(b)} cy={zeroY} r={3.2} fill="none" stroke="#d9a13b" strokeWidth={1.5} />
        ))}

      {/* Axis: spot at each edge and at current spot; $ at top/zero/bottom. Minimal by design --
          this is a payoff shape, not a data chart, so ticks orient rather than enumerate. */}
      <text x={m.l} y={height - 6} fontSize={10} fill="#82878f" fontFamily="Consolas, monospace">
        {x0.toFixed(0)}
      </text>
      <text x={width - m.r} y={height - 6} fontSize={10} fill="#82878f" fontFamily="Consolas, monospace" textAnchor="end">
        {x1.toFixed(0)}
      </text>
      {spot !== null && spot >= x0 && spot <= x1 && (
        <text x={sx(spot)} y={height - 6} fontSize={10} fill="#a6adb8" fontFamily="Consolas, monospace" textAnchor="middle">
          {spot.toFixed(0)}
        </text>
      )}
      <text x={m.l - 6} y={sy(yMax) + 3} fontSize={10} fill="#82878f" fontFamily="Consolas, monospace" textAnchor="end">
        {fmtMoney0(yMax)}
      </text>
      <text x={m.l - 6} y={zeroY + 3} fontSize={10} fill="#82878f" fontFamily="Consolas, monospace" textAnchor="end">
        $0
      </text>
      <text x={m.l - 6} y={sy(yMin) + 3} fontSize={10} fill="#82878f" fontFamily="Consolas, monospace" textAnchor="end">
        {fmtMoney0(yMin)}
      </text>

      {hoverSpot !== null && hoverPnl !== null && (
        <>
          <line x1={hoverX!} y1={m.t} x2={hoverX!} y2={height - m.b} stroke="#eceff3" strokeWidth={1} opacity={0.4} />
          <circle cx={hoverX!} cy={sy(hoverPnl)} r={3} fill="#eceff3" />
          <text
            x={hoverX! > width - 140 ? hoverX! - 8 : hoverX! + 8}
            y={m.t + 12}
            fontSize={11}
            fill="#eceff3"
            fontFamily="Consolas, monospace"
            textAnchor={hoverX! > width - 140 ? "end" : "start"}
          >
            {hoverSpot.toFixed(2)} · {fmtMoney0(hoverPnl)}
          </text>
        </>
      )}
    </svg>
  );
}
