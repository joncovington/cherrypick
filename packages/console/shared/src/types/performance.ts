// ------------------------------------------------------------------- module performance (shared)
// The suite-wide performance read every module shares: one calibration reading per profile tag,
// via `server/src/readers/performance.ts::readModulePerformance` / `GET /api/performance/:module`.
// Field names inside `reading` stay snake_case, matching `core.metrics`' own `calibration_reading`
// JSON verbatim, rather than a ~20-key hand mapping to camelCase.

import type { MeasurementBreak } from "./modules.js";

export const PERFORMANCE_MODULE_SCHEMA = {
  meic: "meic_ic",
  flies: "fly_book",
  earnings: "earnings",
  calendars: "dc_week",
  pmcc: "pmcc_99",
  curve: "curve_vx",
  bwb: "bwb_132",
} as const;

export type PerformanceModuleId = keyof typeof PERFORMANCE_MODULE_SCHEMA;

export interface ModulePerformanceGroup {
  tag: string;
  reading: Record<string, unknown>;
  sessionNets: Array<[string, number]>;
  tradeNets: number[];
}

export interface ExitReasonRow {
  tag: string;
  reason: string;
  n: number;
  net: number | null;
  avgNet: number | null;
}

export interface HeldBackRow {
  tag: string;
  action: string;
  reason: string;
  gate: string | null;
  n: number;
}

export interface AdvisedPair {
  advised: string;
  base: string;
  /** Sessions BOTH books actually recorded a net for, in this read's window -- not the advised
   * book's trade count and not the experiment's `sessions_run` (which counts a loop APPLYING the
   * artifact, not a session with paired data to compare). */
  sessionsPaired: number;
  experimentId: string | null;
  /** `null` when no experiment row was found to ask (a pair can exist without a live experiment --
   * config-authored `advised:` books are not unheard of); `true`/`false` is the stored verdict's
   * own answer once one has been computed. */
  underpowered: boolean | null;
}

export interface ExcursionPosition {
  id: string;
  tag: string;
  symbol: string;
  mae: number;
  mfe: number;
  n: number | null;
}

export interface ExcursionsDistribution {
  median: number | null;
  n: number;
}

export interface ExcursionsData {
  positions: ExcursionPosition[];
  maeDistribution: ExcursionsDistribution;
  mfeDistribution: ExcursionsDistribution;
}

export interface ExcursionsResult {
  ok: boolean;
  data: ExcursionsData | null;
  error: string | null;
}

export interface ModulePerformanceResult {
  ok: boolean;
  module: PerformanceModuleId;
  schema: string;
  era: { key: "current" | "ALL"; from: string | null; note: string | null };
  nRecords: number;
  groups: ModulePerformanceGroup[];
  /** Realized exit reasons per tag, or `{unavailable}` for a module with no single exit-reason
   * concept (flies) -- `readers/exitReasons.ts`, read directly (a query, not `metricsBridge`). */
  exitReasons: ExitReasonRow[] | { unavailable: string };
  /** What an execution gate held back before a verdict could act. Always an array, including
   * empty -- a module with no management-events table (MEIC) has a real "nothing held back," not
   * an unavailable read the way `exitReasons` can be. */
  heldBack: HeldBackRow[];
  /** Each `advised:<base>` twin paired to its control, with the experiment that produced it --
   * `readers/pairs.ts`. Always an array; empty when the module runs no advised books right now. */
  pairs: AdvisedPair[];
  /** Dates results either side must never be pooled -- `readers/integrity.ts::readMeasurementBreaks`
   * against this module's own `paper_trades.db`, the same table every module's own reader already
   * surfaces (`readers/meic.ts`, ...). Empty when the ledger has none recorded, not unavailable --
   * a module with a clean history is a real state. */
  breaks: MeasurementBreak[];
  /** MAE/MFE per closed position -- `services/excursionsBridge.ts`. `ok: false` for a module with
   * no Python excursions support (meic/flies/bwb), never a fabricated empty result. */
  excursions: ExcursionsResult;
  error: string | null;
}
