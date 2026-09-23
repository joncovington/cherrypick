import type { CSSProperties } from "react";

/**
 * One sparkline for the whole package.
 *
 * Three of these existed independently when this landed -- `Charts.tsx::Sparkline` (cumulative,
 * 120x26, one consumer), a local one in `OpeningRangeCard` (raw, 260x48, dotted), and an inline
 * one in `MeicDeepCards` -- each with its own scale closures. They are being folded in one at a
 * time; this is the survivor.
 *
 * **`domain` is the honest-comparison prop and the reason this is worth sharing.** Sparklines
 * scaled independently lie by omission: an arm swinging +/-$20 and one swinging +/-$400 draw the
 * identical wiggle, and a reader comparing a column of tiles reads them as equals. Few's
 * *Best Practices for Scaling Sparklines* is the long form of the argument. So a row of sparks
 * meant to be compared passes one `domain`, or states its own range in the card's foot line --
 * never neither.
 *
 * Under `minPoints` it renders nothing. A single point drawn as a line is a trend that does not
 * exist, which is the same failure as a zero standing in for a missing measurement.
 */
export function Spark({
  values,
  mode = "raw",
  domain,
  width = 120,
  height = 26,
  tone = "sign",
  title,
  pointTitles,
  minPoints = 2,
  stretch = true,
}: {
  values: number[];
  /** `cumulative` plots the running sum -- the trajectory of a per-session net series. */
  mode?: "raw" | "cumulative";
  /** Shared y-extent. Values outside it are clamped, so a shared scale cannot silently rescale. */
  domain?: [number, number];
  width?: number;
  height?: number;
  /** `sign` colours by where the series ENDS relative to zero; the rest are explicit. */
  tone?: "sign" | "pos" | "neg" | "muted";
  title?: string;
  /** One per value. Present means draw a dot per point carrying that text. */
  pointTitles?: string[];
  minPoints?: number;
  /** Fill the container (default), or keep the aspect ratio and cap at `width`. */
  stretch?: boolean;
}) {
  if (values.length < minPoints) return null;

  let running = 0;
  const series = mode === "cumulative" ? values.map((v) => (running += v)) : values;

  // A cumulative series is read against zero, so zero belongs in the extent whether or not the
  // data reaches it. A raw one (prices, rates) is not.
  const lo = domain?.[0] ?? Math.min(...series, ...(mode === "cumulative" ? [0] : []));
  const hi = domain?.[1] ?? Math.max(...series, ...(mode === "cumulative" ? [0] : []));
  const span = hi - lo || 1;

  const pad = 2;
  const X = (i: number) => (i * width) / Math.max(series.length - 1, 1);
  const Y = (v: number) => {
    const clamped = Math.min(Math.max(v, lo), hi);
    return pad + (1 - (clamped - lo) / span) * (height - pad * 2);
  };

  const ends = series[series.length - 1] ?? 0;
  const stroke =
    tone === "pos"
      ? "var(--ok)"
      : tone === "neg"
        ? "var(--err)"
        : tone === "muted"
          ? "var(--text-muted)"
          : ends >= 0
            ? "var(--ok)"
            : "var(--err)";

  const clamped = series.some((v) => v < lo || v > hi);
  const label = title ?? "trend";
  const style: CSSProperties = stretch
    ? { width: "100%", height, display: "block" }
    : { width: "100%", maxWidth: width, height: "auto", display: "block" };

  return (
    <svg
      viewBox={`0 0 ${width} ${height}`}
      role="img"
      aria-label={label}
      style={style}
      preserveAspectRatio={stretch ? "none" : undefined}
    >
      {lo < 0 && hi > 0 && (
        <line
          x1={0}
          y1={Y(0)}
          x2={width}
          y2={Y(0)}
          stroke="var(--text-muted)"
          strokeWidth={0.5}
          strokeDasharray="2 2"
        />
      )}
      <polyline
        fill="none"
        stroke={stroke}
        strokeWidth={1.4}
        points={series.map((v, i) => `${X(i).toFixed(1)},${Y(v).toFixed(1)}`).join(" ")}
      />
      {pointTitles !== undefined &&
        series.map((v, i) => (
          <circle key={i} cx={X(i)} cy={Y(v)} r={2} fill={stroke}>
            <title>{pointTitles[i] ?? ""}</title>
          </circle>
        ))}
      <title>{clamped ? `${label} (clamped to the shared scale)` : label}</title>
    </svg>
  );
}
