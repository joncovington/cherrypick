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
}

export interface SliceReports {
  dir: string;
  reports: SliceReport[];
}
