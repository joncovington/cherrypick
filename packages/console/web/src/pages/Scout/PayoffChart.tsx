interface Props {
  curve: Array<{ spot: number; pnl: number }>;
  slopes: { below: number; above: number };
  breakevens: number[];
  spot: number | null;
  width?: number;
  height?: number;
}

/**
 * Exact payoff diagram: the curve is piecewise linear with kinks only at
 * strikes, so we render the true polyline (tails extrapolated with the
 * analytic slopes) — profit region tinted green, loss red, via clip paths
 * split at the zero line.
 */
export function PayoffChart({ curve, slopes, breakevens, spot, width = 720, height = 280 }: Props) {
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
  const m = { l: 8, r: 8, t: 10, b: 10 };

  const sx = (x: number) => m.l + ((x - x0) / (x1 - x0)) * (width - m.l - m.r);
  const sy = (y: number) => m.t + ((yMax - y) / ySpan) * (height - m.t - m.b);

  const line = points.map((p) => `${sx(p.x).toFixed(1)},${sy(p.y).toFixed(1)}`).join(" ");
  const zeroY = sy(0);
  const fill = `${sx(points[0]!.x).toFixed(1)},${zeroY.toFixed(1)} ${line} ${sx(points[points.length - 1]!.x).toFixed(1)},${zeroY.toFixed(1)}`;

  return (
    <svg
      viewBox={`0 0 ${width} ${height}`}
      role="img"
      aria-label="payoff diagram"
      style={{ width: "100%", height: "auto", display: "block" }}
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
    </svg>
  );
}
