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
  /** The performance group's tag. Since 2026-09-17 each experiment writes its own book,
   * `advised:<experiment name>` (earnings `advised:<name>:<strategy>`); `core.metrics` groups rows
   * stamped with their experiment as `<tag>@<experiment id>`, and rows written before the change
   * carry the legacy `advised:<base>`. Every shape resolves through `readers/experimentIndex.ts`. */
  advised: string;
  /** The book this pair is measured against -- from the experiment's own row (`base_profile`),
   * or for a legacy tag no experiment claims, the base the tag itself names. */
  base: string;
  /** How the experiment was attributed: the stamp on the rows, the tag against the experiment's
   * own, or not at all (a legacy group). */
  attribution: "stamp" | "tag" | "none";
  /** True when no experiment could be attributed at all: a legacy `advised:<base>` group whose
   * rows may span several experiments -- the Advisor page's stored verdicts are the
   * per-experiment read for that history. */
  unstamped: boolean;
  /** Sessions BOTH books actually recorded a net for, in this read's window -- not the advised
   * book's trade count and not the experiment's `sessions_run` (which counts a loop APPLYING the
   * artifact, not a session with paired data to compare). */
  sessionsPaired: number;
  experimentId: string | null;
  experimentName: string | null;
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
  /** Each advised book paired to the base it shadows, one per experiment, with the experiment
   * that produced it -- `readers/pairs.ts`. Always an array; empty when the module has no advised
   * books in this window. */
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
