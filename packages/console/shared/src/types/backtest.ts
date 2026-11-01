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
  /** The unlock bar since 2026-10-25: each check's measure within tolerance of the recording's.
   *  Empty on a report written before it. */
  vendor_within_tolerance?: Record<string, boolean>;
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

/** One table of a store: rows and the span of its first date-like column. Counts only. */
export interface BacktestStoreTable {
  rows: number;
  span: [string, string] | null;
}

export interface BacktestInventoryStore {
  file: string;
  holds?: string;
  built_by?: string;
  used_by?: string;
  present?: boolean;
  script?: string | null;
  bytes?: number;
  tables?: Record<string, BacktestStoreTable>;
  /** Changed since its last count: `tables` is that count, until the backtest-inventory job runs. */
  pending?: boolean;
  error?: string;
}

export interface BacktestInventory {
  dir?: string;
  stores: BacktestInventoryStore[];
  /** Files in the vendor directory nobody declared: a fault until stores.py lists them. */
  undeclared: BacktestInventoryStore[];
  error?: string;
}

export interface BacktestOverview {
  ok: true;
  license: BacktestLicense;
  windows: { design: [string, string]; holdout: [string, string] } & Record<string, [string, string]>;
  stores: BacktestStore[];
  /** Every vendor store against the declared registry (backtest stores.py). */
  inventory?: BacktestInventory;
  validation: BacktestValidation[];
  runs: BacktestRunListing[];
  trials: Record<string, number>;
  /**
   * Distinct specs run as provisional looks, per module (2026-10-30). A provisional run records no
   * trial, so this is how much the history has been looked at while `trials` still reads zero. For
   * the reader only: no statistic is deflated by it. Absent from an older module.
   */
  provisional_looks?: Record<string, number>;
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

/** One session on an arm's path: its net, the running total, and dollars below the running peak. */
export interface BacktestEquityPoint {
  session: string;
  net: number;
  cum: number;
  drawdown: number;
}

/** A drawdown from the peak it fell from to the session that regained it. `peak` null: the run's start. */
export interface BacktestDrawdownPeriod {
  peak: string | null;
  peak_cum: number;
  depth: number;
  /** Depth as a share of peak equity on the cards' account; absent without one. */
  depth_pct?: number | null;
  trough: string;
  recovered: string | null;
  sessions_to_trough: number;
  sessions_to_recover: number | null;
}

export interface BacktestYearRow {
  year: string;
  net: number;
  sessions: number;
  traded: number;
  winning: number;
  max_drawdown: number;
}

/** Wins and losses over the sessions that traded, or over trades. */
export interface BacktestAnatomy {
  count: number;
  wins: number;
  losses: number;
  win_rate: number | null;
  avg_win: number | null;
  avg_loss: number | null;
  payoff_ratio: number | null;
  best: number | null;
  worst: number | null;
  best_session?: string | null;
  worst_session?: string | null;
  skew?: number | null;
  /** Non-excess: a normal distribution reads 3. */
  kurtosis?: number | null;
  /** Trades whose net was never recorded, counted apart. */
  not_recorded?: number;
}

export interface BacktestExitRow {
  outcome: string;
  count: number;
  share: number;
  win_rate: number | null;
  avg_net: number | null;
  total_net: number;
}

export interface BacktestEdgeTrend {
  split: string;
  mean_first: number;
  mean_second: number;
  change: number;
  ci95: [number, number] | null;
  verdict: "improving" | "decaying" | "within noise" | null;
}

/** The arm's dollars against the cards' account: fixed lots, never compounded. Fractions, not percents. */
export interface BacktestAccountRead {
  capital: number;
  ending: number;
  return: number;
  years: number;
  annual_return: number | null;
  max_drawdown: number;
  return_to_drawdown: number | null;
  /** The first session the equity reached zero: the account would have stopped there. */
  ruined_on?: string | null;
}

export interface BacktestArmCards {
  equity: BacktestEquityPoint[];
  max_drawdown: number;
  drawdowns: BacktestDrawdownPeriod[];
  calendar: { months: Record<string, number>; years: BacktestYearRow[] };
  sessions: BacktestAnatomy;
  trades: BacktestAnatomy;
  /** Null when no trade carries a credit (flies: a legged book has no single credit). */
  premium: BacktestPremium | null;
  /** Null when the schema records no outcome. */
  exits: BacktestExitRow[] | null;
  /** Null under the minimum session count for a verdict. */
  edge_trend: BacktestEdgeTrend | null;
  /** The most any one of this arm's sessions held at risk; null when its schema records none. */
  peak_risk: number | null;
  account: BacktestAccountRead | null;
  /** The compounded lens; null without an account, absent from an older backtest package. */
  compounded?: BacktestCompounded | null;
  /** The sizing check; null unless an account was chosen, absent from an older backtest package. */
  sizing?: BacktestSizing | null;
}

/**
 * `cards.sizing`: a SIZING CHECK, never a verdict. The most whole units of the replay's lots whose
 * 99th-percentile bootstrapped drawdown over `horizon_sessions` stays within `dd_pct` of the account,
 * and what that size risks a session. Percents are percents here (5.4 is 5.4%).
 */
export interface BacktestSizing {
  account: number;
  dd_pct: number;
  /** Under the bootstrap's minimum evidence: no size is given. */
  insufficient: boolean;
  evidence?: number;
  min_evidence?: number;
  units?: number;
  drawdown_p99_per_unit?: number;
  horizon_sessions?: number;
  evidence_sessions?: number;
  /** How far recorded stops filled past their trigger, as cost / trigger (1.29 = 29% past). */
  overshoot?: { n: number; p50: number; p95: number; p99: number } | null;
  unit_risk_max_loss?: number | null;
  unit_risk_stop_tail?: number | null;
  risk_pct_max_loss?: number | null;
  risk_pct_stop_tail?: number | null;
}

/** One curve of the compounded lens. Fractions, not percents. */
export interface BacktestCompoundedMode {
  ending: number;
  /** Null when the run spans no time. -1 when the account stopped. */
  cagr: number | null;
  max_drawdown: number;
  max_drawdown_pct: number;
  /** The session the equity reached zero: the account would have stopped there. */
  stopped_on: string | null;
}

/**
 * `cards.compounded`: a LENS, not what the replay ran, never an input to a verdict. The same sessions
 * re-sized each session to put `risk_pct` percent of equity at risk, a unit being the lots the replay
 * held that session, its risk the session's own recorded risk. Assumes P&L, fills and fees scale with
 * size. `whole` floors to whole units (how it would trade); `fractional` is the upper bound.
 */
export interface BacktestCompounded {
  start: number;
  risk_pct: number;
  /** What a unit's risk is: the positions' max loss, or what their stops lose at the trigger (MEIC). */
  unit?: BacktestRiskUnit;
  years: number;
  whole: BacktestCompoundedMode & { units_min: number; units_max: number; sessions_rounded_to_none: number };
  fractional: BacktestCompoundedMode;
  /** Sessions that traded with no recorded risk: they cannot be sized and are left out of both curves. */
  sessions_unsized: number;
  path: { session: string; whole: number; fractional: number }[];
}

/** Premium collected and kept, over the trades that carry a credit. Fractions, not percents. */
export interface BacktestPremium {
  n: number;
  premium: number;
  /** Trading fees, settlement apart. */
  fees: number;
  fees_not_recorded: number;
  /** fees / premium collected. */
  fee_drag: number | null;
  /** net / the structures' own ceilings (`core.metrics.capture_rate`). */
  capture_rate: number | null;
  capture_n: number;
}

/** `cards.py`: every arm's cards. A chosen capital is one account for every arm; with none
 *  (`basis: "peak_risk"`), each arm reads against its own peak session risk. */
export interface BacktestRunCards {
  ok: boolean;
  error?: string;
  account?: { capital: number | null; basis: "chosen" | "peak_risk"; risk_pct?: number; risk_unit?: string; dd_pct?: number };
  arms?: Record<string, BacktestArmCards>;
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
  /** The run's cards, read beside the report (absent from an older backtest package). */
  cards?: BacktestRunCards;
  [k: string]: unknown;
}

/** The strict shape of a run id (`runner.py`: `%Y%m%d-%H%M%S-<6 hex>`). Checked on the server
 *  before anything is spawned, so a path-like id never reaches the subprocess. */
export const BACKTEST_RUN_ID = /^[0-9]{8}-[0-9]{6}-[0-9a-f]{6}$/;

export function isBacktestRunId(v: string): boolean {
  return BACKTEST_RUN_ID.test(v);
}

/** A chosen account for a run's cards: whole dollars, one to ten digits, no leading zero. Checked
 *  on the server before it is handed to the subprocess. */
export const BACKTEST_CAPITAL = /^[1-9][0-9]{0,9}$/;

export function isBacktestCapital(v: string): boolean {
  return BACKTEST_CAPITAL.test(v);
}

/** The compounded lens's risk per session, in percent: above 0, at most 100, two decimals at most.
 *  Checked on the server before it is handed to the subprocess. */
export const BACKTEST_RISK_PCT = /^(100|[1-9]?[0-9])(\.[0-9]{1,2})?$/;

export function isBacktestRiskPct(v: string): boolean {
  return BACKTEST_RISK_PCT.test(v) && Number(v) > 0 && Number(v) <= 100;
}

/** The lens's default risk per session, in percent: the backtest package's own default. */
export const BACKTEST_DEFAULT_RISK_PCT = 2;

/** The sizing check's default drawdown cap, in percent of the account: the backtest package's own. Its
 *  shape is checked as a risk percent is (`isBacktestRiskPct`). */
export const BACKTEST_DEFAULT_DD_PCT = 30;

/** The lens's risk units, as the backtest package names them (`cards.RISK_UNITS`). */
export const BACKTEST_RISK_UNITS = ["max_loss", "stop"] as const;
export type BacktestRiskUnit = (typeof BACKTEST_RISK_UNITS)[number];

export function isBacktestRiskUnit(v: string): v is BacktestRiskUnit {
  return (BACKTEST_RISK_UNITS as readonly string[]).includes(v);
}

/** An arm's name as a run records it (`pm-200`, `live-1400`, `advised:x-1`). Checked on the server
 *  before it is handed to the subprocess, which also refuses an arm the run does not have. */
export const BACKTEST_ARM = /^[A-Za-z0-9][A-Za-z0-9:._+-]{0,79}$/;

export function isBacktestArm(v: string): boolean {
  return BACKTEST_ARM.test(v);
}

/** One trade in a run's trade log (`cards.trade_log`): the ledger's own fields, as
 *  `core.ledgers` names them. A field the module does not record is null, never zero. */
export interface BacktestTrade {
  session: string;
  entry_time: string | null;
  exit_time: string | null;
  /** How it ended, in the module's own words. */
  outcome: string | null;
  gross_pnl: number | null;
  fees: number | null;
  settlement: number | null;
  slippage: number | null;
  net_pnl: number | null;
  entry_credit: number | null;
  capital: number | null;
  position_id: string | null;
}

/** `read trades --id --arm`: one arm's trades, oldest first. */
export interface BacktestTradeLog {
  run_id: string;
  arm: string;
  trades: BacktestTrade[];
}

/** A trade's session (`YYYY-MM-DD`) and position id (`PAPER-d16-SPX-2022...`), checked for shape on
 *  the server before the trade path is spawned. */
export const BACKTEST_SESSION = /^[0-9]{4}-[0-9]{2}-[0-9]{2}$/;
export const BACKTEST_POSITION = /^[A-Za-z0-9][A-Za-z0-9:._+-]{0,119}$/;

export function isBacktestSession(v: string): boolean {
  return BACKTEST_SESSION.test(v);
}

export function isBacktestPosition(v: string): boolean {
  return BACKTEST_POSITION.test(v);
}

/** One stored sample of a trade's path (`tradepath.path`): each side's cost to close at mid (its exit
 *  price once closed), the position's P&L at those marks, spot and VIX at or before the sample. A
 *  missing leg quote leaves the mark and the P&L null: a gap, never a neighbour's value. */
export interface BacktestPathPoint {
  ts: number;
  time: string;
  spot: number | null;
  put_mark: number | null;
  call_mark: number | null;
  pnl: number | null;
  vix: number | null;
}

/** `read path`: one MEIC condor through its session, from entry to its last exit. */
export interface BacktestTradePath {
  run_id: string;
  arm: string;
  session: string;
  position_id: string;
  /** The store file the quotes were re-read from. */
  store: string;
  /** [long, short] for the put side, [short, long] for the call side. */
  strikes: { put: [number, number]; call: [number, number] };
  credit: { put: number; call: number };
  entry_time: string | null;
  exits: { side: string; time: string | null; reason: string | null; price: number | null }[];
  points: BacktestPathPoint[];
  gaps: number;
}
