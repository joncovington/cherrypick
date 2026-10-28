/**
 * The book-review read surfaces (docs/trading-book-review-plan.md): B10's execution record and B5's
 * slice reports. Both are computed on the Python side; the console renames and renders.
 */

/** B10: one week of a live arm's execution beside its P&L (`cherrypick execution --json`). */
export interface ExecutionWeek {
  week: string;
  net: number;
  trades: number;
  orders: number;
  /** Filled / (filled + missed); null with no decided order. */
  fillRate: number | null;
  /** Sum over filled orders of (fill - mid at submit) in dollars; positive = better than the model. */
  slipDollars: number | null;
  slipPctWidthMean: number | null;
  minutesToFillMean: number | null;
  overrides: { kind: string; session: string | null; at: string | null }[];
  clean: boolean;
}

export interface ExecutionGap {
  since: string | null;
  sessions: number;
  /** Live net - paper mirror net over the sessions live was working. */
  total: number | null;
  parts: Record<string, number>;
  largest: string | null;
}

export interface ExecutionModule {
  module: string;
  ok: boolean;
  reason: string | null;
  mirror: string | null;
  weeks: ExecutionWeek[];
  gap: ExecutionGap | null;
  window: ExecutionWindow | null;
}

/** B1's rolling-expectancy trigger, reported only: the last `window` live trades against the paper
 *  mirror's mean. `would` is normal / watch / alarm / too_few, or null with no baseline. */
export interface ExecutionWindow {
  window: number;
  n: number;
  mean: number | null;
  se: number | null;
  baseline: number | null;
  z: number | null;
  would: string | null;
}

export interface ExecutionRecord {
  modules: ExecutionModule[];
}

/** B5: one cell of a slice report. */
export interface SliceCell {
  value: string;
  n: number;
  mean: number | null;
  ci95: [number, number] | null;
  greyed: boolean;
}

export interface SliceReport {
  file: string;
  label: string;
  unit: string;
  trades: number;
  cells: number;
  rankedCells: number;
  overall: SliceCell;
  dimensions: { name: string; cells: SliceCell[] }[];
  note: string;
  /** The plain-English reading of the report (2026-10-28), derived by the reader from the cells. */
  analysis: SliceAnalysis;
}

/** How one dimension reads:
 *  - `one-group`: every trade fell in one group, so there is nothing to compare;
 *  - `too-few`: only one group (or none) has 30+ trades;
 *  - `separated`: two ranked groups' 95% intervals do not overlap (`best` is the one whose interval
 *    sits higher, `worst` the lower);
 *  - `overlapping`: every ranked group's interval overlaps another's, so a gap could be chance
 *    (`best`/`worst` are the highest and lowest means, for the sentence). */
export interface SliceDimensionRead {
  name: string;
  read: "one-group" | "too-few" | "separated" | "overlapping";
  best: SliceCell | null;
  worst: SliceCell | null;
  ranked: number;
  greyed: number;
}

export interface SliceAnalysis {
  /** From the label `<schema>/<arm>/<month or all>`; null when the label does not split. */
  schema: string | null;
  arm: string | null;
  month: string | null;
  /** The overall interval against zero: all above, all below, or crossing it. */
  overall: "positive" | "negative" | "unclear";
  dimensions: SliceDimensionRead[];
  greyedCells: number;
}

export interface SliceReports {
  dir: string;
  reports: SliceReport[];
}
