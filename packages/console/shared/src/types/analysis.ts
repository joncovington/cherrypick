// ------------------------------------------------------------------- strategy analysis (shared)
// One analysis per arm tag, from `python -m cherrypick.core.analysis read` via
// `server/src/readers/analysis.ts::readModuleAnalysis` / `GET /api/analysis/:module`
// (docs/strategy-analysis-plan.md). Field names inside `analysis` stay the bridge's snake_case,
// verbatim, as `ModulePerformanceGroup.reading` does.

import type { PerformanceModuleId } from "./performance.js";
import type { TradingMode } from "./status.js";

/** The `core.analysis.VERSION` this console renders. Any other is refused, never half-drawn. */
export const ANALYSIS_VERSION = 1;

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
