/**
 * Signal studies (`packages/backtest`'s `signalstudy.py`, #1179): a signal's firings and what the
 * underlying did after each, against a control of sessions that had not fired by then. Read through
 * `python -m cherrypick.backtest read signals | signal --name`. The shapes are the module's own;
 * nothing here is recomputed.
 */

/** One result in brief (`read signals`). */
export interface SignalStudyBrief {
  name: string;
  symbol: string;
  source: "history" | "recorder" | "vendor";
  kind: string;
  bar_minutes: number | null;
  first: string | null;
  last: string | null;
  firings: number;
  licensed: boolean;
  generated_at: string | null;
}

export interface SignalStudyList {
  studies: SignalStudyBrief[];
  unreadable: string[];
  /** Licensed results the server left out because the backtest feature is off. */
  withheld: number;
}

export interface SignalDistanceRead {
  unit: "points" | "pct" | "em";
  value: number;
  n: number;
  up?: number | null;
  down?: number | null;
  either?: number | null;
}

/** One side (the firings or the control) at one horizon. Absent fields mean no observations. */
export interface SignalSideRead {
  n: number;
  mean_pct?: number | null;
  median_pct?: number | null;
  sd_pct?: number | null;
  up_share?: number | null;
  mean_up_pct?: number | null;
  mean_down_pct?: number | null;
  em_n?: number;
  mean_range_em?: number | null;
  beyond_em_share?: number | null;
  distances?: SignalDistanceRead[];
}

export interface SignalDifference {
  mean_pct: number;
  t: number | null;
}

export interface SignalHorizonRead {
  horizon: number;
  fired: SignalSideRead;
  control: SignalSideRead;
  difference: SignalDifference | null;
  incomplete: number;
}

export interface SignalYearRead {
  year: string;
  horizons: {
    horizon: number;
    fired_n: number;
    fired_mean_pct: number | null;
    control_n: number;
    control_mean_pct: number | null;
    difference: SignalDifference | null;
  }[];
}

export interface SignalFiringOutcome {
  move_pct: number;
  up_pct: number;
  down_pct: number;
  range_em: number | null;
}

/** One firing: its session, minute (since midnight Eastern), price and value, and `h<N>` outcomes. */
export interface SignalFiringRow {
  session: string;
  minute: number;
  price: number;
  value: number | null;
  [horizon: `h${number}`]: SignalFiringOutcome | null;
}

export interface SignalStudyRequest {
  name: string;
  signal: Record<string, unknown>;
  symbol: string;
  source: "history" | "recorder" | "vendor";
  bar_minutes?: number;
  horizons: number[];
  distances: { unit: "points" | "pct" | "em"; value: number }[];
  from?: string;
  through?: string;
}

export interface SignalStudyResult {
  ok: true;
  name: string;
  request: SignalStudyRequest;
  licensed: boolean;
  em_index: string | null;
  bars: { count: number; first: string | null; last: string | null; sessions: number };
  dropped_sessions: { not_trading_day: number; incomplete: number };
  firings_n: number;
  firing_sessions: number;
  control_n: number;
  horizons: SignalHorizonRead[];
  by_year: SignalYearRead[];
  firings: SignalFiringRow[];
  source?: { store: string; em_file: string | null; reads_holdout?: boolean };
  generated_at?: string;
}

/** A study name as `signalstudy.validate` spells one; the server checks it before anything spawns. */
export const SIGNAL_STUDY_NAME = /^[a-z0-9][a-z0-9_-]{0,63}$/;

export function isSignalStudyName(v: string): boolean {
  return SIGNAL_STUDY_NAME.test(v);
}
