/**
 * The arm comparison (`/compare`): every arm beside its control, as `python -m
 * cherrypick.core.compare --json` states it (schema 1).
 *
 * Field names stay snake_case, verbatim from the bridge -- the `core.metrics` convention
 * (`metricsBridge.ts`): ~45 statistical fields hand-mapped to camelCase would be a second place for
 * their names to drift from `core.stats`, which owns what each one means. The console computes none
 * of them and labels nothing better or worse.
 */

export const COMPARE_SCOPES = ["era", "all"] as const;

/** `era` is each module's own evidence window (the default); `all` is every row the ledger holds. */
export type CompareScope = (typeof COMPARE_SCOPES)[number];

export function isCompareScope(v: unknown): v is CompareScope {
  return typeof v === "string" && (COMPARE_SCOPES as readonly string[]).includes(v);
}

/** One journaled measurement break, as the module wrote it. */
export interface CompareBreak {
  break_date: string;
  kind: string;
  reason: string;
}

export interface CompareEra {
  /** The first session in scope; null when no break bounds the module (every row counts). */
  start: string | null;
  bounding_break: CompareBreak | null;
  /** Arms whose own start is later than the era's (an `arm_added` break). */
  arm_starts: Record<string, string>;
  /** Breaks in the window that do NOT bound it (roster notes, damaged sessions). */
  caveats: CompareBreak[];
  /** Breaks dated after the session, which bind nothing yet. */
  ignored_future: CompareBreak[];
  /** pmcc's era stamp, where the module stamps its era on each row. */
  era_stamp: string | null;
}

/** `core.stats.paired_comparison` on one per-session series: the block every pair carries. */
export interface ComparePairedBlock {
  /** Sessions both arm and control closed, inside the overlap of their windows. */
  sessions: number;
  first: string | null;
  last: string | null;
  pairing: string;
  mean_diff: number | null;
  sd_diff: number | null;
  total_diff: number | null;
  ci: [number, number] | null;
  ci_level: number;
  ci_excludes_zero: boolean | null;
  /** One-sided t lower bound on the mean difference, at `alpha`. */
  t_lower: number | null;
  alpha: number;
  arm_better: number;
  control_better: number;
  ties: number;
  sign_test_p: number | null;
  /** Projection at the observed mean and spread, not a forecast. */
  sessions_to_exclude_zero: number | null;
  edge: number | null;
  power: number;
  sessions_needed: number | null;
  mde: number | null;
}

/** The dollar pair (net per session), plus how it was keyed and what it left out. */
export interface CompareVsControl extends ComparePairedBlock {
  paired_on: "session" | "entry_session";
  arm_only_sessions: number;
  control_only_sessions: number;
  /**
   * The same pair on each session's net per entry, keyed and windowed as the dollar pair, so a
   * capped arm beside a much larger control is not read as a size difference. Optional: absent on
   * a document written before the bridge carried it.
   */
  per_entry?: ComparePairedBlock | null;
  /** The same pair on each session's net per $100 of recorded capital; null when either side's
   *  ledger records none on the shared sessions (never a zero). */
  per_100_risk?: ComparePairedBlock | null;
}

export interface CompareArm {
  arm: string;
  advised: boolean;
  experiment_id: string | null;
  experiment_status: string | null;
  start: string | null;
  start_reason: "era" | "arm_added" | "era_stamp" | "first_row";
  trades: number;
  sessions: number;
  first: string | null;
  last: string | null;
  gross: number | null;
  /** Positive dollars, subtracted from gross (the document's `costs_note` says what is in it). */
  costs: number | null;
  net: number | null;
  /** 0..1 */
  win_rate: number | null;
  /** Positive dollars: the peak-to-trough fall of the per-SESSION equity path. */
  max_drawdown: number | null;
  mean_session_net: number | null;
  sd_session_net: number | null;
  session_net_ci: [number, number] | null;
  session_net_ci_excludes_zero: boolean | null;
  sessions_to_exclude_zero: number | null;
  /** Size-normalised (optional: absent on a document written before the bridge carried them). */
  entries_per_session?: number | null;
  net_per_entry?: number | null;
  /** Summed capital at risk (max loss / buying power as the module's ledger records it), over the
   *  trades that record one; null when none does. */
  capital?: number | null;
  capital_known_trades?: number;
  /** Net per $100 of that capital, over the same trades; null when none records one. */
  net_per_100_risk?: number | null;
  trade_nets: number[];
  session_nets: Array<[string, number]>;
  control: string | null;
  vs_control: CompareVsControl | null;
}

export interface CompareModule {
  module: string;
  trade_schema: string;
  status: "ok" | "absent" | "failed";
  reason: string | null;
  /** Absent on an `absent`/`failed` module: the bridge never reached the window. */
  scope?: CompareScope;
  era: CompareEra | null;
  arms: CompareArm[];
}

export interface CompareDoc {
  schema: 1;
  generated_at: string;
  session: string;
  scope: CompareScope;
  /** Interval level, e.g. 0.95. */
  level: number;
  resamples: number;
  seed: number;
  pairing: string;
  costs_note: string;
  modules: CompareModule[];
}

/** `GET /api/compare?scope=` -- a failed bridge is `{ok: false, error}`, never an empty document. */
export interface ComparePayload {
  ok: boolean;
  scope: CompareScope;
  data: CompareDoc | null;
  error: string | null;
}
