import { useState } from "react";
import { AXIS_MUTED } from "../../components/Charts";

export interface GexStrikeRow {
  strike: number;
  call_iv: number;
  put_iv: number;
  call_oi: number;
  put_oi: number;
  call_vol: number;
  put_vol: number;
  total_vol: number;
  call_gex: number;
  put_gex: number;
  net_gex: number;
  abs_gex: number;
  call_gex_vol: number;
  put_gex_vol: number;
  net_gex_vol: number;
}

export type GexView = "net" | "oivol" | "abs";

/** The strike-range control: "auto", or a band of ± that percent of spot (symbol-agnostic, so
 *  XSP and an ETF get the same reach SPX does; the old fixed ±120 points was ±1.5% of SPX). */
export const GEX_RANGES = ["auto", "0.5", "1", "1.5", "3"] as const;
export type GexRange = (typeof GEX_RANGES)[number];

/** A strike carries "meaningful" GEX at or above this share of the chart's largest bar. */
const MEANINGFUL_SHARE = 0.02;

/**
 * Which strikes the chart draws. "auto" fits the strikes whose GEX is at least 2% of the largest
 * bar (a 0DTE chain has tiny non-zero GEX far out, so "non-zero" alone is nearly the whole chain),
 * spot, and today's spot trail, padded by three strikes each side. A band keeps strikes within
 * ±pct of spot. Exported so the rule is pinned without a DOM.
 */
export function gexStrikeWindow(
  series: GexStrikeRow[],
  spot: number,
  range: GexRange,
  magnitude: (r: GexStrikeRow) => number,
  trail: number[] = [],
): GexStrikeRow[] {
  const sorted = [...series].sort((a, b) => a.strike - b.strike);
  if (range !== "auto") {
    const band = (Number(range) / 100) * spot;
    return sorted.filter((r) => Math.abs(r.strike - spot) <= band).sort((a, b) => b.strike - a.strike);
  }
  const max = Math.max(...sorted.map(magnitude), 0);
  const meaningful = sorted.filter((r) => max > 0 && magnitude(r) >= max * MEANINGFUL_SHARE).map((r) => r.strike);
  const anchors = [...meaningful, spot, ...trail];
  const lo = Math.min(...anchors);
  const hi = Math.max(...anchors);
  let first = sorted.findIndex((r) => r.strike >= lo);
  let last = sorted.length - 1 - [...sorted].reverse().findIndex((r) => r.strike <= hi);
  if (first < 0) first = 0;
  if (last >= sorted.length) last = sorted.length - 1;
  return sorted.slice(Math.max(0, first - 3), Math.min(sorted.length, last + 4)).sort((a, b) => b.strike - a.strike);
}

export function fmtGexDollars(v: number): string {
  const abs = Math.abs(v);
  const sign = v < 0 ? "-" : "";
  if (abs >= 1e9) return `${sign}$${(abs / 1e9).toFixed(2)}B`;
  if (abs >= 1e6) return `${sign}$${(abs / 1e6).toFixed(1)}M`;
  if (abs >= 1e3) return `${sign}$${(abs / 1e3).toFixed(0)}K`;
  return `${sign}$${abs.toFixed(0)}`;
}

interface Props {
  series: GexStrikeRow[];
  view: GexView;
  spot: number;
  zeroGamma: number | null;
  callWall: number | null;
  putWall: number | null;
  /** Today's intraday spot trail (epoch seconds), drawn across the session width. */
  spotHistory?: Array<{ ts: number; spot: number }>;
  /** ET trading-session bounds for the trail's time axis (9:30–16:00). */
  spotSession?: { date: string; openTs: number; closeTs: number } | null;
  height?: number;
}

/**
 * GEX by strike — the old page's Chart.js rendering reproduced: the FULL
 * strike ladder on the y axis (non-zero range padded 3, then |strike−spot| ≤
 * 120 like the reference), thin 3px bars, faint vertical gridlines, a rotated
 * axis title, and a hover tooltip with per-basis values.
 */
export function GexProfileChart({ series, view, spot, zeroGamma, callWall, putWall, spotHistory, spotSession }: Props) {
  const [hover, setHover] = useState<{ index: number; px: number; py: number; w: number; h: number } | null>(null);
  const [range, setRange] = useState<GexRange>("auto");

  const nonZero = series.filter((s) => s.abs_gex !== 0 || s.net_gex_vol !== 0 || s.total_vol !== 0);
  if (nonZero.length === 0) return <p className="muted">no strikes with GEX data</p>;
  const magnitude = (r: GexStrikeRow): number =>
    Math.max(Math.abs(r.net_gex), Math.abs(r.abs_gex), Math.abs(r.net_gex_vol));
  const rows = gexStrikeWindow(nonZero, spot, range, magnitude, (spotHistory ?? []).map((h) => h.spot));
  const rangeControl = (
    <div className="gex-range-row">
      <label className="muted lbl" title="auto fits the strikes carrying GEX, spot and today's spot trail; a band is ± that share of spot">
        strike range{" "}
        <select className="text-input" value={range} onChange={(e) => setRange(e.target.value as GexRange)}>
          {GEX_RANGES.map((r) => (
            <option key={r} value={r}>
              {r === "auto" ? "auto" : `±${r}%`}
            </option>
          ))}
        </select>
      </label>
    </div>
  );
  if (rows.length === 0)
    return (
      <>
        {rangeControl}
        <p className="muted">no strikes in this range</p>
      </>
    );

  // Wide viewBox so the SVG renders near 1:1 in the card — stretched-up
  // viewBoxes are what made the fonts look oversized.
  const width = 1150;
  const hasTimeAxis = spotSession != null && spotHistory !== undefined && spotHistory.length > 1;
  const rowH = 13;
  const m = { l: 64, r: 16, t: 8, b: hasTimeAxis ? 22 : 8 };
  const height = m.t + m.b + rows.length * rowH;
  const plotW = width - m.l - m.r;
  const values = (r: GexStrikeRow): number[] =>
    view === "net" ? [r.net_gex] : view === "abs" ? [r.abs_gex] : [r.net_gex, r.net_gex_vol];
  const maxAbs = Math.max(...rows.flatMap((r) => values(r).map(Math.abs)), 1);
  const zeroX = m.l + plotW / 2;
  const sx = (v: number) => zeroX + (v / maxAbs) * (plotW / 2) * 0.96;
  const strikeY = (strike: number): number | null => {
    // Interpolate between category rows for the overlay lines.
    for (let i = 0; i < rows.length - 1; i++) {
      const hi = rows[i]!.strike;
      const lo = rows[i + 1]!.strike;
      if (strike <= hi && strike >= lo) {
        const t = hi === lo ? 0 : (hi - strike) / (hi - lo);
        return m.t + (i + 0.5 + t) * rowH;
      }
    }
    return null;
  };

  interface Overlay {
    y: number;
    color: string;
    dash?: string;
    label: string;
    side: "left" | "right";
    filled: boolean;
  }
  // Old-page tag styling: filled wall pills hug the left axis; spot and
  // zero-gamma ride the right edge as outlined value tags.
  const overlays: Overlay[] = [];
  const spotY = strikeY(spot);
  if (spotY !== null)
    overlays.push({ y: spotY, color: "var(--accent)", label: `$${spot.toFixed(2)}`, side: "right", filled: false });
  if (zeroGamma !== null) {
    const y = strikeY(zeroGamma);
    if (y !== null)
      overlays.push({ y, color: "var(--amber-line)", dash: "3 3", label: `Zero Γ: $${zeroGamma.toFixed(2)}`, side: "right", filled: false });
  }
  if (callWall !== null) {
    const y = strikeY(callWall);
    if (y !== null)
      overlays.push({ y, color: "var(--pos)", dash: "6 4", label: callWall.toFixed(2), side: "left", filled: true });
  }
  if (putWall !== null) {
    const y = strikeY(putWall);
    if (y !== null)
      overlays.push({ y, color: "var(--neg)", dash: "3 3", label: putWall.toFixed(2), side: "left", filled: true });
  }

  const seriesMeta = (j: number): { label: string; color: string } => {
    if (view === "abs") return { label: "Abs GEX", color: "var(--accent)" };
    if (view === "net") return { label: "Net GEX", color: "var(--pos)" };
    return j === 0 ? { label: "Net GEX (OI)", color: "var(--pos)" } : { label: "Net GEX (Volume)", color: "color-mix(in srgb, var(--pos) 55%, var(--surface))" };
  };
  const barColor = (v: number, j: number): string =>
    view === "abs" ? "var(--accent)" : v >= 0 ? (j === 0 ? "var(--pos)" : "color-mix(in srgb, var(--pos) 55%, var(--surface))") : j === 0 ? "var(--neg)" : "color-mix(in srgb, var(--neg) 55%, var(--surface))";

  const hovered = hover !== null ? rows[hover.index] : undefined;

  return (
    <>
    {rangeControl}
    <div className="gex-chart-fit" style={{ position: "relative" }}>
    <svg
      viewBox={`0 0 ${width} ${height}`}
      role="img"
      aria-label="GEX by strike"
      preserveAspectRatio="xMidYMid meet"
      style={{ width: "100%", height: "100%", display: "block" }}
      onMouseLeave={() => setHover(null)}
    >
      {/* faint vertical gridlines, chart.js-style */}
      {[0.125, 0.25, 0.375, 0.5, 0.625, 0.75, 0.875].map((f) => (
        <line key={f} x1={m.l + f * plotW} y1={m.t} x2={m.l + f * plotW} y2={height - m.b} stroke="var(--chart-grid)" />
      ))}
      <line x1={zeroX} y1={m.t} x2={zeroX} y2={height - m.b} stroke="var(--chart-grid-mid)" />
      <text
        x={10}
        y={m.t + (height - m.t - m.b) / 2}
        fontSize={9}
        fill={AXIS_MUTED}
        textAnchor="middle"
        transform={`rotate(-90 10 ${m.t + (height - m.t - m.b) / 2})`}
      >
        Strike Price
      </text>
      {rows.map((r, i) => {
        const yMid = m.t + (i + 0.5) * rowH;
        const vals = values(r);
        return (
          <g key={r.strike}>
            <text x={m.l - 6} y={yMid + 2.5} textAnchor="end" fontSize={8.5} fill={AXIS_MUTED} fontFamily="Consolas, monospace">
              {r.strike}
            </text>
            {vals.map((v, j) => {
              const barH = 3;
              const gap = vals.length > 1 ? 1 : 0;
              const total = vals.length * barH + (vals.length - 1) * gap;
              const y = yMid - total / 2 + j * (barH + gap);
              return (
                <rect
                  key={j}
                  x={Math.min(zeroX, sx(v))}
                  y={y}
                  width={Math.max(1, Math.abs(sx(v) - zeroX))}
                  height={barH}
                  fill={barColor(v, j)}
                />
              );
            })}
            {/* row-wide hover target */}
            <rect
              x={m.l}
              y={m.t + i * rowH}
              width={plotW}
              height={rowH}
              fill="transparent"
              onMouseEnter={(e) => {
                const host = (e.currentTarget.ownerSVGElement?.parentElement ?? null) as HTMLElement | null;
                const rect = host?.getBoundingClientRect();
                if (rect !== undefined) {
                  setHover({ index: i, px: e.clientX - rect.left, py: e.clientY - rect.top, w: rect.width, h: rect.height });
                }
              }}
            />
          </g>
        );
      })}
      {hasTimeAxis && (() => {
        // Trail across the plot width, anchored to the ET trading session:
        // x = (ts − 9:30 ET) / (16:00 − 9:30), y = spot interpolated on the
        // strike axis. A full session records thousands of ticks — decimate
        // to ~400 points so the SVG stays light.
        const { openTs, closeTs } = spotSession!;
        const span = Math.max(closeTs - openTs, 1);
        const stride = Math.max(1, Math.floor(spotHistory.length / 400));
        const sampled = spotHistory.filter((_, i) => i % stride === 0 || i === spotHistory.length - 1);
        const pts = sampled
          .map((h) => {
            const y = strikeY(h.spot);
            if (y === null) return null;
            const frac = Math.min(1, Math.max(0, (h.ts - openTs) / span));
            return `${(m.l + frac * plotW).toFixed(1)},${y.toFixed(1)}`;
          })
          .filter((s): s is string => s !== null);
        const hours = [
          ["9:30", 0],
          ["10:30", 1 / 6.5],
          ["11:30", 2 / 6.5],
          ["12:30", 3 / 6.5],
          ["13:30", 4 / 6.5],
          ["14:30", 5 / 6.5],
          ["15:30", 6 / 6.5],
          ["16:00", 1],
        ] as const;
        return (
          <>
            {pts.length > 1 && (
              <polyline points={pts.join(" ")} fill="none" stroke="var(--accent)" strokeWidth={1} opacity={0.45} />
            )}
            {hours.map(([label, frac]) => {
              const x = m.l + frac * plotW;
              return (
                <g key={label}>
                  <line x1={x} y1={height - m.b} x2={x} y2={height - m.b + 4} stroke={AXIS_MUTED} />
                  <text
                    x={x}
                    y={height - m.b + 14}
                    textAnchor={frac === 0 ? "start" : frac === 1 ? "end" : "middle"}
                    fontSize={9}
                    fill={AXIS_MUTED}
                    fontFamily="Consolas, monospace"
                  >
                    {label}
                  </text>
                </g>
              );
            })}
            <text x={m.l} y={m.t + 9} fontSize={9} fill="var(--accent)" opacity={0.8}>
              spot trail — {spotSession!.date} ET
            </text>
          </>
        );
      })()}
      {overlays.map((o, i) => {
        const tagH = 13;
        const tagW = o.label.length * 5.6 + 10;
        const tagX = o.side === "left" ? m.l - 4 : width - m.r - tagW;
        const tagY = o.y - tagH / 2;
        return (
          <g key={i}>
            <line x1={m.l} y1={o.y} x2={width - m.r} y2={o.y} stroke={o.color} strokeWidth={1.2} strokeDasharray={o.dash} />
            <rect
              x={tagX}
              y={tagY}
              width={tagW}
              height={tagH}
              rx={3}
              fill={o.filled ? o.color : "var(--surface-sunken)"}
              stroke={o.color}
              strokeWidth={1}
            />
            <text
              x={tagX + tagW / 2}
              y={o.y + 3}
              textAnchor="middle"
              fontSize={9}
              fontWeight={700}
              fill={o.filled ? "var(--canvas)" : o.color}
              fontFamily="Consolas, monospace"
            >
              {o.label}
            </text>
          </g>
        );
      })}
    </svg>
    {hovered !== undefined && hover !== null && (
      <div
        style={{
          position: "absolute",
          // Kept inside the chart: above the cursor in its lower half, left of it in its right
          // part. Hanging below the cursor at the bottom strikes overflowed the card and gave the
          // page a vertical scrollbar.
          ...(hover.py > hover.h / 2 ? { bottom: hover.h - hover.py + 10 } : { top: hover.py + 10 }),
          ...(hover.px > hover.w * 0.6 ? { right: hover.w - hover.px + 14 } : { left: hover.px + 14 }),
          background: "var(--surface-sunken)",
          border: "1px solid var(--border)",
          borderRadius: 6,
          padding: "6px 10px",
          pointerEvents: "none",
          fontSize: 12,
          fontFamily: "Consolas, monospace",
          whiteSpace: "nowrap",
          zIndex: 5,
        }}
      >
        <div style={{ fontWeight: 700, marginBottom: 2 }}>{hovered.strike}</div>
        {values(hovered).map((v, j) => {
          const meta = seriesMeta(j);
          return (
            <div key={j} style={{ display: "flex", alignItems: "center", gap: 6 }}>
              <span style={{ width: 9, height: 9, background: barColor(v, j), display: "inline-block", borderRadius: 2 }} />
              <span>
                {meta.label}: {fmtGexDollars(v)}
              </span>
            </div>
          );
        })}
      </div>
    )}
    </div>
    </>
  );
}

/**
 * Generic horizontal mirrored-bar chart by strike: positive series right,
 * negative left. Drives the OI-by-strike and Volume-by-strike cards.
 */
export function StrikeBarsChart({
  series,
  spot,
  bars,
  height = 380,
}: {
  series: GexStrikeRow[];
  spot: number;
  bars: Array<{ label: string; color: string; value: (r: GexStrikeRow) => number }>;
  height?: number;
}) {
  const active = series.filter((s) => bars.some((b) => b.value(s) !== 0));
  const rows = [...active]
    .sort((a, b) => Math.abs(a.strike - spot) - Math.abs(b.strike - spot))
    .slice(0, 40)
    .sort((a, b) => b.strike - a.strike);
  if (rows.length === 0) return <p className="muted">no data</p>;
  const width = 760;
  const m = { l: 64, r: 16, t: 16, b: 8 };
  const plotW = width - m.l - m.r;
  const rowH = (height - m.t - m.b) / rows.length;
  const maxAbs = Math.max(...rows.flatMap((r) => bars.map((b) => Math.abs(b.value(r)))), 1);
  const zeroX = m.l + plotW / 2;
  const sx = (v: number) => zeroX + (v / maxAbs) * (plotW / 2) * 0.96;
  const spotIdx = rows.findIndex((r, i) => i < rows.length - 1 && spot <= r.strike && spot >= rows[i + 1]!.strike);
  return (
    <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label="by strike" style={{ width: "100%", height: "auto", display: "block" }}>
      <line x1={zeroX} y1={m.t} x2={zeroX} y2={height - m.b} stroke="var(--chart-grid-mid)" />
      {bars.map((b, i) => (
        <text key={b.label} x={m.l + i * 92} y={10} fontSize={10} fill={b.color}>
          {b.label}
        </text>
      ))}
      {rows.map((r, i) => {
        const yMid = m.t + (i + 0.5) * rowH;
        return (
          <g key={r.strike}>
            {rowH >= 11 && (
              <text x={m.l - 6} y={yMid + 3} textAnchor="end" fontSize={10} fill={AXIS_MUTED} fontFamily="Consolas, monospace">
                {r.strike}
              </text>
            )}
            {bars.map((b, j) => {
              const v = b.value(r);
              if (v === 0) return null;
              const barH = Math.max(2, (rowH * 0.75) / bars.length);
              const y = yMid - (rowH * 0.375) + j * barH;
              return (
                <rect key={j} x={Math.min(zeroX, sx(v))} y={y} width={Math.max(1, Math.abs(sx(v) - zeroX))} height={barH - 0.5} fill={b.color}>
                  <title>{`${r.strike} ${b.label}: ${Math.abs(v).toLocaleString()}`}</title>
                </rect>
              );
            })}
          </g>
        );
      })}
      {spotIdx >= 0 && (
        <line x1={m.l} y1={m.t + (spotIdx + 1) * rowH} x2={width - m.r} y2={m.t + (spotIdx + 1) * rowH} stroke="var(--accent)" strokeWidth={1.2} />
      )}
    </svg>
  );
}

/** Call IV vs put IV by strike — line chart with a spot marker. */
export function IvSkewChart({ series, spot, height = 240 }: { series: GexStrikeRow[]; spot: number; height?: number }) {
  const rows = series.filter((s) => s.call_iv > 0 || s.put_iv > 0).sort((a, b) => a.strike - b.strike);
  if (rows.length < 2) return <p className="muted">no IV data</p>;
  const width = 760;
  const m = { l: 44, r: 12, t: 10, b: 20 };
  const xs = rows.map((r) => r.strike);
  const ivs = rows.flatMap((r) => [r.call_iv, r.put_iv]).filter((v) => v > 0);
  const x0 = Math.min(...xs);
  const x1 = Math.max(...xs);
  const y0 = Math.min(...ivs);
  const y1 = Math.max(...ivs);
  const sx = (x: number) => m.l + ((x - x0) / (x1 - x0)) * (width - m.l - m.r);
  const sy = (y: number) => m.t + ((y1 - y) / Math.max(y1 - y0, 0.01)) * (height - m.t - m.b);
  const line = (key: "call_iv" | "put_iv") =>
    rows
      .filter((r) => r[key] > 0)
      .map((r) => `${sx(r.strike).toFixed(1)},${sy(r[key]).toFixed(1)}`)
      .join(" ");
  return (
    <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label="IV skew" style={{ width: "100%", height: "auto", display: "block" }}>
      {spot >= x0 && spot <= x1 && (
        <line x1={sx(spot)} y1={m.t} x2={sx(spot)} y2={height - m.b} stroke="var(--amber-line)" strokeDasharray="4 4" />
      )}
      <polyline points={line("call_iv")} fill="none" stroke="var(--pos)" strokeWidth={1.6} />
      <polyline points={line("put_iv")} fill="none" stroke="var(--neg)" strokeWidth={1.6} />
      <text x={m.l} y={height - 6} fontSize={10} fill={AXIS_MUTED}>{x0}</text>
      <text x={width - m.r} y={height - 6} textAnchor="end" fontSize={10} fill={AXIS_MUTED}>{x1}</text>
      <text x={m.l} y={m.t + 8} fontSize={10} fill="var(--pos)">call IV%</text>
      <text x={m.l + 60} y={m.t + 8} fontSize={10} fill="var(--neg)">put IV%</text>
    </svg>
  );
}
