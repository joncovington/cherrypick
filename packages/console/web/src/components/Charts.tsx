import { fmtMoney } from "./DataTable";
import { niceTicks } from "./chart/scales";

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

// Re-exported for the ~existing call sites that import niceTicks from here -- the one
// implementation now lives in components/chart/scales.ts, alongside the rest of the kit.
export { niceTicks };

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
 * a bad result look smaller than it is). It was shared by the Champions arm comparison and Review's
 * per-arm table -- those two used to solve the identical stated problem with two different bar
 * mechanics (a zero-centered track vs. a plain 0-100% left-anchored one with no zero reference at
 * all). Champions was removed 2026-08-20, so Review is the remaining caller; `compact` fits inside
 * a table cell, and the default size still matches a standalone row.
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

/**
 * A cumulative sparkline: the running sum of `values` drawn bare, no axes, sized to sit inside a
 * stat tile. Cumulative rather than per-period on purpose — a row of per-session bars at this size
 * is noise, while the running total shows the trajectory, which is the one thing a tile-sized chart
 * can carry honestly. Colored by where the line ENDS (up or down overall), with a zero reference
 * whenever the path crosses it.
 *
 * Under `minPoints` (default 2) it renders nothing at all: a single point is a dot, and a dot drawn
 * as a trend is the "one session dressed as a result" failure the Review page's own styling rule
 * warns about.
 */
export function Sparkline({
  values,
  width = 120,
  height = 26,
  minPoints = 2,
  title,
}: {
  values: number[];
  width?: number;
  height?: number;
  minPoints?: number;
  title?: string;
}) {
  if (values.length < minPoints) return null;
  let running = 0;
  const cum = values.map((v) => (running += v));
  const lo = Math.min(...cum, 0);
  const hi = Math.max(...cum, 0);
  const span = hi - lo || 1;
  const X = (i: number) => (i * width) / Math.max(cum.length - 1, 1);
  const Y = (v: number) => 2 + (1 - (v - lo) / span) * (height - 4);
  const up = (cum[cum.length - 1] ?? 0) >= 0;
  return (
    <svg
      viewBox={`0 0 ${width} ${height}`}
      role="img"
      aria-label={title ?? "trend"}
      style={{ width: "100%", height, display: "block" }}
      preserveAspectRatio="none"
    >
      {lo < 0 && hi > 0 && (
        <line x1={0} y1={Y(0)} x2={width} y2={Y(0)} stroke={AXIS_MUTED} strokeWidth={0.5} strokeDasharray="2 2" />
      )}
      <polyline
        fill="none"
        stroke={up ? "#43b57a" : "#d95c4a"}
        strokeWidth={1.3}
        points={cum.map((v, i) => `${X(i).toFixed(1)},${Y(v).toFixed(1)}`).join(" ")}
      />
      {title !== undefined && <title>{title}</title>}
    </svg>
  );
}

/**
 * A number line with labeled markers — for price levels whose SPATIAL relationship is the point
 * (spot against the gamma flip and the walls). Read as a table, "6380 flip / 6450 call wall /
 * 6400 spot" makes a reader do the arithmetic; drawn, "just above the flip, well short of the call
 * wall" is immediate.
 *
 * Levels with a null value are dropped rather than placed at zero. Under two placeable levels it
 * renders nothing — one point on a number line shows no relationship, which is the only thing this
 * chart is for. `marker` is drawn distinctly (the "you are here" reading) and participates in the
 * extent so it is never off the strip.
 */
export function LevelStrip({
  levels,
  marker,
  height = 54,
}: {
  levels: Array<{ label: string; value: number | null; color?: string }>;
  marker?: { label: string; value: number | null; muted?: boolean };
  height?: number;
}) {
  const placed = levels.filter((l): l is { label: string; value: number; color?: string } => l.value !== null);
  const markerValue = marker?.value ?? null;
  if (placed.length < 2) return null;
  const all = [...placed.map((l) => l.value), ...(markerValue !== null ? [markerValue] : [])];
  const lo = Math.min(...all);
  const hi = Math.max(...all);
  const span = hi - lo || 1;
  const pad = span * 0.12;
  const width = 720;
  const m = { l: 40, r: 40 };
  const X = (v: number) => m.l + ((v - (lo - pad)) / (span + pad * 2)) * (width - m.l - m.r);
  const axisY = height - 22;
  return (
    <svg
      viewBox={`0 0 ${width} ${height}`}
      role="img"
      aria-label="price levels"
      style={{ width: "100%", height: "auto", display: "block" }}
    >
      <line x1={m.l} y1={axisY} x2={width - m.r} y2={axisY} stroke={AXIS_MUTED} strokeWidth={0.75} />
      {placed.map((l) => (
        <g key={l.label}>
          <line
            x1={X(l.value)}
            y1={axisY - 8}
            x2={X(l.value)}
            y2={axisY + 5}
            stroke={l.color ?? AXIS_MUTED}
            strokeWidth={1.4}
          />
          <text x={X(l.value)} y={axisY + 16} textAnchor="middle" {...AXIS_FONT}>
            {l.label}
          </text>
          <text x={X(l.value)} y={axisY - 12} textAnchor="middle" {...AXIS_FONT} fill={l.color ?? AXIS_MUTED}>
            {Math.round(l.value).toLocaleString()}
          </text>
        </g>
      ))}
      {markerValue !== null && marker !== undefined && (
        <g opacity={marker.muted === true ? 0.55 : 1}>
          <polygon
            points={`${X(markerValue)},${axisY - 3} ${X(markerValue) - 5},${axisY - 13} ${X(markerValue) + 5},${axisY - 13}`}
            fill="#d9a13b"
          />
          <text x={X(markerValue)} y={axisY - 17} textAnchor="middle" {...AXIS_FONT} fill="#d9a13b">
            {marker.label} {Math.round(markerValue).toLocaleString()}
          </text>
        </g>
      )}
    </svg>
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
