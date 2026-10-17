// ------------------------------------------------------------------- Backtest (bridged)
// `packages/backtest`'s read surface (`python -m cherrypick.backtest read overview|run`), through
// `server/src/services/backtestBridge.ts`. The results come from licensed vendor history whose
// licence forbids redistribution, so this page is shown ONLY when the orchestrator's features
// answer says `features.backtest === true` (fail closed, unlike every other page). Field names stay
// the module's own snake_case; where the module's shape is open the type is loose.

/** The licence clock: whether the vendor subscription is live and, once it ends, the purge date. */
export interface BacktestLicense {
  active: boolean;
  terminated_on: string | null;
  purge_by: string | null;
  days_left: number | null;
  overdue: boolean;
}

/** One vendor store: a symbol at one cadence. */
export interface BacktestStore {
  symbol: string;
  cadence: number;
  file: string;
  complete: number;
  span: [string, string] | null;
  refused: number;
  refused_reasons: Record<string, number>;
}

export interface BacktestGapStats {
  median?: number | null;
  p90?: number | null;
  p99?: number | null;
  mean_signed?: number | null;
  [k: string]: unknown;
}

export interface BacktestQuoteAgreement {
  ok: boolean;
  /** Why the layer could not be compared (`no recorder samples`, `no vendor samples`). */
  reason?: string;
  recorded_quotes?: number | null;
  /** Fractions 0-1: recorded quotes the vendor matched, and of those, identical bid and ask. */
  matched?: number | null;
  identical?: number | null;
  mid_gap?: BacktestGapStats | null;
  spot_gap?: BacktestGapStats | null;
  [k: string]: unknown;
}

export interface BacktestProfileAgreement {
  ok: boolean;
  reason?: string;
  /** `*_same` are fractions 0-1 of the paired profiles. */
  pairs?: number | null;
  call_wall_same?: number | null;
  put_wall_same?: number | null;
  net_gex_sign_same?: number | null;
  gamma_flip_gap?: BacktestGapStats | null;
  strikes_per_profile?: { recorder_median?: number | null; vendor_median?: number | null } | null;
  [k: string]: unknown;
}

export interface BacktestSideReading {
  measures: Record<string, unknown> | null;
  checks: Record<string, boolean> | null;
}

/** One cadence's vendor-against-recorder validation. */
export interface BacktestValidation {
  cadence: number | null;
  file: string;
  dates: string[];
  unlocks_history: boolean;
  sessions_compared: number | null;
  vendor_at_least_recorder: Record<string, boolean>;
  recorder: BacktestSideReading;
  vendor: BacktestSideReading;
  sessions: Record<string, { quotes?: BacktestQuoteAgreement; profile?: BacktestProfileAgreement }>;
}

export type BacktestWindow = "validation" | "design" | "holdout";

export interface BacktestRunListing {
  run_id: string;
  created_at: number;
  module: string;
  window: BacktestWindow | string;
  /** A design-window run made before validation unlocked history: no trial, no evidence. */
  provisional?: boolean;
  arms: string[];
  span: [string, string];
  cadence: number;
  sessions: number;
  refused: number;
}

export interface BacktestOverview {
  ok: true;
  license: BacktestLicense;
  windows: { design: [string, string]; holdout: [string, string] } & Record<string, [string, string]>;
  stores: BacktestStore[];
  validation: BacktestValidation[];
  runs: BacktestRunListing[];
  trials: Record<string, number>;
}

/** An arm's whole-run summary: per-session net figures, in whole-position dollars. */
export interface BacktestArmSummary {
  sessions: number;
  total: number | null;
  mean: number | null;
  sd: number | null;
  sharpe_per_session: number | null;
  psr: number | null;
  trials: number | null;
  dsr: number | null;
  min_track_record: number | null;
  mean_ci95_block_bootstrap: [number, number] | null;
  [k: string]: unknown;
}

/** The arm against control, paired by session. */
export interface BacktestVsControl {
  sessions: number;
  mean_diff: number | null;
  sd_diff: number | null;
  total_diff: number | null;
  ci: [number, number] | null;
  ci_level?: number;
  t_lower?: number | null;
  arm_better?: number;
  control_better?: number;
  ties?: number;
  sign_test_p?: number | null;
  arm_only_sessions?: number;
  control_only_sessions?: number;
  [k: string]: unknown;
}

export interface BacktestArmReport {
  spec_hash: string;
  summary: BacktestArmSummary;
  /** Trading costs over the run, whole-position dollars, already out of every net figure. */
  fees?: number | null;
  trades?: number | null;
  /** Null when either side has no dated rows. */
  vs_control?: BacktestVsControl | null;
  no_exposure?: boolean;
  [k: string]: unknown;
}

export interface BacktestRunReport {
  run_id: string;
  window: BacktestWindow | string;
  provisional?: boolean;
  provisional_note?: string;
  span: [string, string];
  cadence: number;
  fill_model: unknown;
  code_commit: string | null;
  data_fingerprint: string | null;
  trials_for_module: number | null;
  refused: Record<string, string>;
  arms: Record<string, BacktestArmReport>;
  [k: string]: unknown;
}

/** The strict shape of a run id (`runner.py`: `%Y%m%d-%H%M%S-<6 hex>`). Checked on the server
 *  before anything is spawned, so a path-like id never reaches the subprocess. */
export const BACKTEST_RUN_ID = /^[0-9]{8}-[0-9]{6}-[0-9a-f]{6}$/;

export function isBacktestRunId(v: string): boolean {
  return BACKTEST_RUN_ID.test(v);
}
