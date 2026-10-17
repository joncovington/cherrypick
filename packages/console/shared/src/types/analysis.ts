// ------------------------------------------------------------------- strategy analysis (shared)
// One analysis per arm tag, from `python -m cherrypick.core.analysis read` via
// `server/src/readers/analysis.ts::readModuleAnalysis` / `GET /api/analysis/:module`
// (docs/strategy-analysis-plan.md). Field names inside `analysis` stay the bridge's snake_case,
// verbatim, as `ModulePerformanceGroup.reading` does.

import type { PerformanceModuleId } from "./performance.js";
import type { TradingMode } from "./status.js";

/** The `core.analysis.VERSION` this console renders. Any other is refused, never half-drawn.
 *  2 (2026-10-17): each arm is read over its own era where the module's breaks name arms. */
export const ANALYSIS_VERSION = 2;

/** `core.analysis.windows`: where an arm's reading starts, and why. `arm_era` is the arm's own era
 *  (its latest break, else the module's latest book-wide one); `suite` is the suite window, either
 *  because nothing has broken since it opened or because the module's breaks name no arm. */
export interface ArmWindow {
  scope: "arm_era" | "suite";
  start: string | null;
  break: { break_date: string | null; scope: string | null; kind: string | null; reason: string | null } | null;
  reason: string;
}

export interface AnalysisStreaks {
  longest_win: number;
  longest_loss: number;
  current: { kind: "win" | "loss"; length: number } | null;
}

/** One fall below a previous high. `peak_session` null: the high was the start, before any
 *  trade. An open episode has no recovery, so its recovering lengths are null. */
export interface DrawdownEpisode {
  peak_session: string | null;
  peak_equity: number;
  low_session: string;
  low_equity: number;
  depth: number;
  recovered_session: string | null;
  open: boolean;
  sessions_falling: number;
  sessions_recovering: number | null;
  days_falling: number | null;
  days_recovering: number | null;
}

export interface DurabilityRow {
  fraction: number;
  /** ceil(fraction x trades): how many winners the fraction asks to remove. */
  asked: number;
  /** How many were removed -- fewer than `asked` when there were fewer winners. */
  removed: number;
  removed_net: number;
  net: number;
  sign_flips: boolean;
}

export interface StressReading {
  historical: number;
  reordered: number | null;
  grouped: number | null;
  sessions: number;
  quantile: number;
  resamples: number;
  block: number;
  seed: number;
  /** Why the reshuffled figures are null (a short sample), or null when they were computed. */
  withheld: string | null;
}

/** `core.analysis.capital.peak_risk`: the most the arm had at risk at once. */
export interface PeakRiskReading {
  peak: number | null;
  session: string | null;
  at: string | null;
  trades: number;
  capital_known: number;
  untimed: number;
  basis: "session" | "intraday" | "mixed" | null;
  withheld: string | null;
}

export interface AllocationDeclaration {
  from: string;
  capital: number;
  note: string | null;
}

/** `core.analysis.capital.allocation_reading`: return and drawdown against the declared allocation
 *  in force each session. `return`/`max_drawdown` are fractions; null with no declaration. */
export interface AllocationReading {
  declared: AllocationDeclaration[];
  sessions: number;
  covered: number;
  uncovered: number;
  return: number | null;
  max_drawdown: number | null;
  max_drawdown_dollars: number | null;
  current: number | null;
  capital_over_max_drawdown: number | null;
}

export interface CapitalReading {
  peak_risk: PeakRiskReading;
  return_on_peak_risk: number | null;
  max_drawdown_over_peak_risk: number | null;
  allocation: AllocationReading;
}

/** `core.analysis.execution_sensitivity`: the net at multiples of the slippage each module modelled,
 *  over the trades that record slippage (`scope` "covered" when that is not all of them). */
export interface ExecutionReading {
  trades: number;
  covered: number;
  scope: "all" | "covered";
  basis: "in_gross" | "cost" | "mixed" | null;
  net: number | null;
  slippage: number | null;
  rows: Array<{ multiple: number; extra: number; net: number; change: number | null }>;
  breakeven_multiple: number | null;
  withheld: string | null;
}

/** One row of `core.analysis.buckets`: the same columns for every dimension. */
export interface BucketRow {
  bucket: string;
  trades: number;
  sessions: number;
  net: number;
  win_rate: number;
  avg_net: number;
  best: number;
  worst: number;
  /** Fewer sessions than `min_sessions`: shown, never read as a finding. */
  thin: boolean;
}

export interface BucketTable {
  rows: BucketRow[];
  /** Records this dimension could not bucket (an entry time the schema does not record). */
  skipped: number;
}

export interface Breakdowns {
  min_sessions: number;
  weekday: BucketTable;
  hour: BucketTable;
  month: BucketTable;
  event: BucketTable;
}

export interface TradeRef {
  net: number;
  session: string | null;
  entry_time: string | null;
}

export interface ArmAnalysis {
  version: number;
  reading: Record<string, unknown>;
  best_trade: TradeRef | null;
  worst_trade: TradeRef | null;
  streaks: AnalysisStreaks;
  session_streaks: AnalysisStreaks;
  drawdowns: {
    count: number;
    open: DrawdownEpisode | null;
    deepest: DrawdownEpisode[];
    longest_closed: DrawdownEpisode | null;
  };
  durability: { net: number; trades: number; without_top: DurabilityRow[] };
  stress: StressReading;
  /** Optional only so a reply from before the capital block renders without it. */
  capital?: CapitalReading;
  /** Optional for the same reason: replies from before the slippage ladder. */
  execution?: ExecutionReading;
  /** Optional for the same reason: replies from before the breakdowns. */
  buckets?: Breakdowns;
  /** The window this arm was read over (version 2). */
  window?: ArmWindow;
  coverage: { trades: number; entry_time: number; exit_time: number; entry_spot: number; exit_spot: number };
}

export interface ModuleAnalysisGroup {
  tag: string;
  analysis: ArmAnalysis;
}

export interface ModuleAnalysisResult {
  ok: boolean;
  module: PerformanceModuleId;
  mode: TradingMode;
  schema: string;
  /** The same window the performance slide reads: the suite epoch, and the module's own stamped
   * era where it has one. */
  era: { key: "current" | "ALL"; from: string | null; note: string | null; moduleEra: string | null };
  nRecords: number;
  /** The declared-allocation file the read used, and why it applied nothing if it was refused. */
  allocations: { path: string | null; error: string | null };
  groups: ModuleAnalysisGroup[];
  error: string | null;
}

/**
 * One arm of one module on the Compare page's risk tab (`GET /api/analysis`): fields picked from
 * that arm's `core.analysis` reading, never computed. Every arm over its own window.
 */
export interface ArmRiskRow {
  module: PerformanceModuleId;
  tag: string;
  window: ArmWindow | null;
  trades: number;
  sessions: number;
  net: number | null;
  peakRisk: number | null;
  /** The arm's whole net over its largest peak -- never "return on peak risk" (the performance
   *  slide's Σ net / Σ session peaks). */
  netOverLargestPeak: number | null;
  maxDrawdown: number | null;
  drawdownOverPeak: number | null;
  /** The 95th-percentile drawdown with sessions reordered in blocks; null when withheld. */
  stressBlocks: number | null;
  stressWithheld: string | null;
  withoutTop10: number | null;
  top10SignFlips: boolean;
  currentRun: AnalysisStreaks["current"];
  breakevenMultiple: number | null;
}

export interface ArmsRiskResult {
  mode: TradingMode;
  rows: ArmRiskRow[];
  /** A module whose read failed: named, never an empty section. */
  failures: Array<{ module: PerformanceModuleId; error: string }>;
}
