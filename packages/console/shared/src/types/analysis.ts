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
  groups: ModuleAnalysisGroup[];
  error: string | null;
}
