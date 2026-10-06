/**
 * Daily-series readings (`cherrypick.core.metrics.nav`) as the console receives them. Every count
 * is in DAYS, never trades (`basis: "daily"`); a field whose minimum sample is not met is null,
 * never zero.
 */

export type DatedValue = [date: string, value: number];

/** `nav_reading`: a compounding series (contango's NAV). Rates are fractions (0.074 = 7.4%). */
export interface NavReading {
  basis: "daily";
  days: number;
  start: string | null;
  end: string | null;
  totalReturn?: number | null;
  cagr?: number | null;
  vol?: number | null;
  sharpe?: number | null;
  sortino?: number | null;
  maxDrawdown?: number;
  mar?: number | null;
  ulcerIndex?: number;
  drawdownSpan?: { longest: number; open: number };
  worstDay?: number | null;
  bestDay?: number | null;
  cvar?: number | null;
  skew?: number | null;
  kurtosis?: number | null;
  psr?: number | null;
  minTrackRecordDays?: number | null;
  monthly?: Record<string, number>;
}

/** `equity_reading`: a dollar P&L path that does not compound (curve's marked equity). */
export interface EquityReading {
  basis: "daily";
  days: number;
  start: string | null;
  end: string | null;
  net?: number;
  maxDrawdown?: number;
  drawdownSpan?: { longest: number; open: number };
  worstDay?: number;
  bestDay?: number;
  cvar?: number | null;
  sharpeDaily?: number | null;
  psr?: number | null;
  minTrackRecordDays?: number | null;
  monthly?: Record<string, number>;
}

export interface ContangoArmMetrics {
  startingCapital: number | null;
  reading: NavReading;
  series: DatedValue[];
  /** The arm's own rule replayed over the module's recorded ratios and fund marks. */
  expected: DatedValue[];
  expectedReading: NavReading | null;
  /** NAV / expected - 1 at the latest common session: execution, not the rule. */
  tracking: number | null;
}

export interface ContangoMetrics {
  ok: boolean;
  error: string | null;
  arms: Record<string, ContangoArmMetrics>;
  /** Buy-and-hold of each arm's risk fund, keyed by symbol. */
  benchmarks: Record<string, { series: DatedValue[]; reading: NavReading }>;
}

export interface CurveArmEquity {
  series: DatedValue[];
  /** Sessions an open position carried its previous mark (no usable mark that session). */
  carried: number;
  reading: EquityReading;
}

export interface CurveEquity {
  ok: boolean;
  error: string | null;
  arms: Record<string, CurveArmEquity>;
}
