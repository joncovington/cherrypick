import { fmtMoney } from "./DataTable";

/** The one muted color every hand-rolled SVG chart's axis/legend text should use -- the real
    design token, not each file's own guess at a grey (previously #82878f/#6c7480/#9aa3ad/#5c626d
    scattered across charts for what was meant to be one semantic role). */
export const AXIS_MUTED = "var(--text-muted)";

/** House chart typography: 9px mono, muted, matching the flies/GEX SVGs. */
export const AXIS_FONT = { fontSize: 9, fill: AXIS_MUTED, fontFamily: "Consolas, monospace" } as const;
// No brand accent (#d23f57) in a general-purpose categorical palette -- that color is reserved
// for brand/live/alert moments elsewhere in the app, and a rotating series palette would spend it
// on "just the first series," shown constantly and neutrally.
export const SERIES_COLORS = ["#7aa2ff", "#43b57a", "#d9a13b", "#a06bd9", "#4fc3d9", "#e88a5c", "#8a9c4a"];

export function niceTicks(min: number, max: number, target: number): number[] {
  const span = max - min || 1;
  const raw = span / target;
  const mag = Math.pow(10, Math.floor(Math.log10(Math.max(raw, 1e-9))));
  const step = [1, 2, 5, 10].map((k) => k * mag).find((s) => span / s <= target + 1) ?? 10 * mag;
  const out: number[] = [];
  for (let v = Math.ceil(min / step) * step; v <= max + 1e-9; v += step) out.push(v);
  return out;
}

interface LineSeries {
  label: string;
  color?: string;
  points: Array<{ x: string; y: number }>;
  /** Fill down to zero (equity/underwater style). */
  fill?: string;
}

/**
 * Multi-series line chart over a shared categorical x (dates/periods). Values
 * are y-scaled together so series stay comparable — the point of drawing them
 * on one axis.
 */
export function LineChart({
  series,
  height = 200,
  yFormat = fmtMoney,
  zeroLine = true,
}: {
  series: LineSeries[];
  height?: number;
  yFormat?: (v: number) => string;
  zeroLine?: boolean;
}) {
  const xs = [...new Set(series.flatMap((s) => s.points.map((p) => p.x)))].sort();
  const ys = series.flatMap((s) => s.points.map((p) => p.y));
  if (xs.length < 2 || ys.length === 0) return <p className="muted">not enough history yet</p>;
  const width = 1150;
  const m = { l: 62, r: 12, t: 10, b: 20 };
  const yMin = Math.min(...ys, zeroLine ? 0 : Math.min(...ys));
  const yMax = Math.max(...ys, zeroLine ? 0 : Math.max(...ys));
  const pad = (yMax - yMin || 1) * 0.08;
  const lo = yMin - pad;
  const hi = yMax + pad;
  const X = (x: string) => m.l + (xs.indexOf(x) / (xs.length - 1)) * (width - m.l - m.r);
  const Y = (v: number) => m.t + ((hi - v) / (hi - lo || 1)) * (height - m.t - m.b);

  return (
    <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label="chart" style={{ width: "100%", height: "auto", display: "block" }}>
      {niceTicks(lo, hi, 4).map((v) => (
        <g key={v}>
          <line x1={m.l} y1={Y(v)} x2={width - m.r} y2={Y(v)} stroke={Math.abs(v) < 1e-9 ? "#3d4653" : "#15181e"} />
          <text x={4} y={Y(v) + 3} {...AXIS_FONT}>
            {yFormat(v)}
          </text>
        </g>
      ))}
      {series.map((s, i) => {
        const color = s.color ?? SERIES_COLORS[i % SERIES_COLORS.length]!;
        const pts = s.points.map((p) => `${X(p.x).toFixed(1)},${Y(p.y).toFixed(1)}`).join(" ");
        return (
          <g key={s.label}>
            {s.fill !== undefined && s.points.length > 1 && (
              <polygon
                points={`${X(s.points[0]!.x).toFixed(1)},${Y(0).toFixed(1)} ${pts} ${X(s.points[s.points.length - 1]!.x).toFixed(1)},${Y(0).toFixed(1)}`}
                fill={s.fill}
              />
            )}
            <polyline points={pts} fill="none" stroke={color} strokeWidth={1.6} />
          </g>
        );
      })}
      <text x={m.l} y={height - 6} {...AXIS_FONT}>
        {xs[0]}
      </text>
      <text x={width - m.r} y={height - 6} textAnchor="end" {...AXIS_FONT}>
        {xs[xs.length - 1]}
      </text>
    </svg>
  );
}

/** Signed bar chart over categories, green/red by sign, with an optional cumulative overlay. */
export function BarChart({
  bars,
  overlay,
  height = 200,
  yFormat = fmtMoney,
}: {
  bars: Array<{ x: string; y: number }>;
  overlay?: Array<{ x: string; y: number }>;
  height?: number;
  yFormat?: (v: number) => string;
}) {
  if (bars.length === 0) return <p className="muted">not enough history yet</p>;
  const width = 1150;
  const m = { l: 62, r: 12, t: 10, b: 20 };
  const all = [...bars.map((b) => b.y), ...(overlay?.map((o) => o.y) ?? []), 0];
  const lo = Math.min(...all);
  const hi = Math.max(...all);
  const span = hi - lo || 1;
  const Y = (v: number) => m.t + ((hi + span * 0.08 - v) / (span * 1.16)) * (height - m.t - m.b);
  const bw = (width - m.l - m.r) / bars.length;
  return (
    <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label="chart" style={{ width: "100%", height: "auto", display: "block" }}>
      {niceTicks(lo, hi, 4).map((v) => (
        <g key={v}>
          <line x1={m.l} y1={Y(v)} x2={width - m.r} y2={Y(v)} stroke={Math.abs(v) < 1e-9 ? "#3d4653" : "#15181e"} />
          <text x={4} y={Y(v) + 3} {...AXIS_FONT}>
            {yFormat(v)}
          </text>
        </g>
      ))}
      {bars.map((b, i) => (
        <rect
          key={b.x}
          x={m.l + i * bw + bw * 0.15}
          y={Math.min(Y(0), Y(b.y))}
          width={Math.max(bw * 0.7, 1)}
          height={Math.max(Math.abs(Y(b.y) - Y(0)), 1)}
          fill={b.y >= 0 ? "#43b57a" : "#d95c4a"}
        >
          <title>{`${b.x}: ${yFormat(b.y)}`}</title>
        </rect>
      ))}
      {overlay !== undefined && overlay.length > 1 && (
        <polyline
          points={overlay.map((o, i) => `${(m.l + (i + 0.5) * bw).toFixed(1)},${Y(o.y).toFixed(1)}`).join(" ")}
          fill="none"
          stroke="#7aa2ff"
          strokeWidth={1.6}
        />
      )}
      <text x={m.l} y={height - 6} {...AXIS_FONT}>
        {bars[0]!.x}
      </text>
      <text x={width - m.r} y={height - 6} textAnchor="end" {...AXIS_FONT}>
        {bars[bars.length - 1]!.x}
      </text>
    </svg>
  );
}

/**
 * A zero-centered diverging bar: a loss and a win of the same size read as the same visual weight
 * in opposite colors, off a shared zero tick, rather than scaling losses against wins (which makes
 * a bad result look smaller than it is). Shared by Champions' arm comparison and Review's per-arm
 * table -- those two used to solve the identical stated problem with two different bar mechanics
 * (a zero-centered track vs. a plain 0-100% left-anchored one with no zero reference at all).
 * `compact` fits inside a table cell (Review); the default size matches a standalone row (Champions).
 */
export function SignedBar({
  value,
  maxAbs,
  compact = false,
  className = "",
}: {
  value: number;
  maxAbs: number;
  compact?: boolean;
  className?: string;
}) {
  const half = maxAbs > 0 ? Math.min(50, (Math.abs(value) / maxAbs) * 50) : 0;
  return (
    <span className={`signed-bar ${compact ? "signed-bar-compact" : ""} ${className}`} aria-hidden>
      <span className="signed-bar-zero" />
      <span
        className={`signed-bar-fill ${value >= 0 ? "signed-bar-pos" : "signed-bar-neg"}`}
        style={value >= 0 ? { left: "50%", width: `${half}%` } : { right: "50%", width: `${half}%` }}
      />
    </span>
  );
}

export function SeriesLegend({ items }: { items: Array<{ label: string; color: string }> }) {
  return (
    <div className="forest-legend">
      {items.map((i) => (
        <span key={i.label}>
          <i style={{ background: i.color }} /> {i.label}
        </span>
      ))}
    </div>
  );
}
