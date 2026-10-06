import type { DatedValue } from "@console/shared";

/**
 * Pure helpers over a dated daily series -- what the charts need that the Python reading does not
 * already carry (the reading is one number per metric; a chart needs the path). Every ratio here is
 * a display transform of the series, never a new metric: the metrics are `core.metrics.nav`'s.
 *
 * `kind` says how the series behaves: "nav" compounds (contango's NAV, a fraction of a peak),
 * "equity" is a dollar path from zero that does not (curve's marked equity, dollars below a peak).
 */
export type SeriesKind = "nav" | "equity";

export interface Point {
  x: string;
  y: number;
}

/** Depth below the running peak at each point: a fraction for a NAV (≤ 0), dollars for equity.
 *  Equity's peak starts at `startPeak` (0: the path's own start), so a first-day loss is a drawdown. */
export function underwater(series: DatedValue[], kind: SeriesKind, startPeak = 0): Point[] {
  let peak = kind === "equity" ? startPeak : -Infinity;
  return series.map(([x, v]) => {
    peak = Math.max(peak, v);
    return { x, y: kind === "nav" ? (peak > 0 ? v / peak - 1 : 0) : v - peak };
  });
}

/** The change over a trailing window of `n` points, from the window's first point to each point:
 *  a return for a NAV, a dollar change for equity. Starts once `n` points exist. */
export function rolling(series: DatedValue[], n: number, kind: SeriesKind): Point[] {
  const out: Point[] = [];
  for (let i = n; i < series.length; i++) {
    const a = series[i - n]![1];
    const b = series[i]![1];
    if (kind === "nav" && a <= 0) continue;
    out.push({ x: series[i]![0], y: kind === "nav" ? b / a - 1 : b - a });
  }
  return out;
}

/** A series rescaled to start at `base` -- so arms with different capital, and a benchmark, can
 *  share one axis. */
export function rebase(series: DatedValue[], base: number): Point[] {
  const first = series[0]?.[1];
  if (first === undefined || first <= 0) return [];
  return series.map(([x, v]) => ({ x, y: (v / first) * base }));
}

export interface StressWindow {
  label: string;
  from: string;
  to: string;
  /** Where the window came from: a fixed historical episode, or a live inversion found in the regime series. */
  source: "fixed" | "inversion";
}

/** The suite's named stress episodes for a short-volatility book. A window before a series starts
 *  reads as "not held", never as a zero. */
export const FIXED_STRESS_WINDOWS: StressWindow[] = [
  { label: "Volmageddon", from: "2018-01-26", to: "2018-03-29", source: "fixed" },
  { label: "Covid crash", from: "2020-02-19", to: "2020-04-30", source: "fixed" },
  { label: "Yen-carry unwind", from: "2024-07-16", to: "2024-08-30", source: "fixed" },
];

/** Every stretch of consecutive sessions with the VIX/VIX3M ratio at or above `threshold`: the
 *  module's own live stress episodes, found in its recorded regime series. */
export function inversionWindows(
  rows: Array<{ tradeDate: string; ratio: number | null }>,
  threshold = 1.0,
): StressWindow[] {
  const out: StressWindow[] = [];
  let start: string | null = null;
  let last: string | null = null;
  for (const r of rows) {
    const inverted = r.ratio !== null && r.ratio >= threshold;
    if (inverted && start === null) start = r.tradeDate;
    if (!inverted && start !== null && last !== null) {
      out.push({ label: `inversion ${start}`, from: start, to: last, source: "inversion" });
      start = null;
    }
    last = r.tradeDate;
  }
  if (start !== null && last !== null) out.push({ label: `inversion ${start} (open)`, from: start, to: last, source: "inversion" });
  return out;
}

export interface WindowResult {
  /** Return (nav) or dollar change (equity) from the last point before the window to its last point. */
  change: number | null;
  /** Deepest fall below the peak reached inside the window, same units. */
  drawdown: number | null;
  points: number;
}

/** How a series fared inside one window. The baseline is the last point before the window opens,
 *  so the first day's move counts; a series with no point inside it returns nulls. */
export function inWindow(series: DatedValue[], w: StressWindow, kind: SeriesKind): WindowResult {
  const before = [...series].reverse().find(([d]) => d < w.from);
  const inside = series.filter(([d]) => d >= w.from && d <= w.to);
  if (inside.length === 0) return { change: null, drawdown: null, points: 0 };
  const base = before?.[1] ?? inside[0]![1];
  const path: DatedValue[] = [["base", base], ...inside];
  const end = inside[inside.length - 1]![1];
  const dd = Math.min(...underwater(path, kind, base).map((p) => p.y));
  return {
    change: kind === "nav" ? (base > 0 ? end / base - 1 : null) : end - base,
    drawdown: dd,
    points: inside.length,
  };
}
