import type { MeasurementBreak, TradeMoney, TradeTotals } from "./modules.js";

/**
 * automations' read surface (packages/automations): research item 75's arms in paper. An arm is one
 * configured variant; the set of arms is NOT fixed, so every arm name on this surface comes from the
 * ledger's rows, never from a list here. A position is one structure (any number of legs) entered once
 * a session by one arm and closed by that arm's exit or held to the expiry's posted close.
 *
 * The money is the module's own and adds up on the row: `entryValue + exitValue = grossPnl` and
 * `grossPnl - fees - settlement - slippage = netPnl`, whole-position dollars, signed cash flow. `netPnl`
 * is net at the suite's slippage model (the verdict); `netStress` and `netMid` are context. The console
 * reads them and re-derives only the running sums a curve and a drawdown are made of.
 */

/** One leg of a structure: +1 bought, -1 sold. */
export interface AutomationsLeg {
  role: string;
  right: string;
  side: number;
  strike: number;
}

/** The latest usable mark of an open structure. */
export interface AutomationsMark {
  ts: string;
  /** What closing would bring at the mid, whole position, signed. */
  value: number | null;
  spot: number | null;
}

export interface AutomationsPosition {
  positionId: string;
  arm: string;
  symbol: string;
  status: string;
  expiration: string;
  entrySession: string;
  entryTime: string | null;
  quantity: number;
  entrySpot: number | null;
  /** The structure's net price per share at the mid, signed: a credit is +. */
  entryMid: number | null;
  /** Largest loss, whole position (positive); the capital the position ties up. */
  capital: number | null;
  /** Cash taken in (+) or paid (-) at the mid, whole position. */
  entryValue: number;
  entryFees: number;
  entrySlippage: number;
  exitReason: string | null;
  exitSession: string | null;
  exitSpot: number | null;
  exitValue: number | null;
  settlePrice: number | null;
  grossPnl: number | null;
  fees: number | null;
  settlement: number | null;
  slippage: number | null;
  netPnl: number | null;
  netStress: number | null;
  netMid: number | null;
  legs: AutomationsLeg[];
  /** Latest usable mark of an open position, with the newest spot any mark recorded. */
  mark: AutomationsMark | null;
  /** entryValue + mark.value: what the position is worth now before any cost. Open positions only. */
  unrealised: number | null;
}

/** The gate that stopped an arm on a tick: the engine's first failing check and the value it read. */
export interface AutomationsGate {
  reason: string;
  check: string | null;
  value: string | null;
  /** How many ticks it refused the same way today, from the decision journal. */
  occurrences: number | null;
}

/** One arm's session as the register records it. */
export interface AutomationsEntryRow {
  arm: string;
  symbol: string;
  entrySession: string;
  /** pending (its window is not over), entered, or missed (the window closed unacted). */
  outcome: string;
  /** The last reason a tick in the window gave. */
  refusal: string | null;
  positionId: string | null;
  decidedAt: string | null;
  gate: AutomationsGate | null;
}

export interface AutomationsPayload {
  /** The loop's last session, else the newest session the register names. */
  session: string | null;
  dbPresent: boolean;
  /** Every arm the ledger names, sorted. */
  arms: string[];
  positions: AutomationsPosition[];
  /** The register's rows for `session`: each arm that was due, and how it stands. */
  today: AutomationsEntryRow[];
  measurementBreaks: MeasurementBreak[];
  lastIteration: { ranAt: number; phase: string; status: string; ageSeconds: number } | null;
}

/** A closed structure in the trade table standard: entry + exit = gross; gross - fees - settlement - slippage = net. */
export interface AutomationsHistoryRow extends TradeMoney {
  positionId: string;
  arm: string;
  symbol: string;
  expiration: string;
  entrySession: string;
  exitSession: string | null;
  exitReason: string | null;
  netStress: number | null;
  netMid: number | null;
  legs: AutomationsLeg[];
}

export interface AutomationsHistory {
  rows: AutomationsHistoryRow[];
  totals: TradeTotals;
}

/** One arm's (or all arms') closed trades, net and drawdown. Dollars, whole position. */
export interface AutomationsLine {
  trades: number;
  net: number;
  gross: number;
  fees: number;
  settlement: number;
  slippage: number;
  /** Net with fills halfway from the mid to the natural fill: context. */
  netStress: number;
  /** Net with fills at the mid: context. */
  netMid: number;
  wins: number;
  mean: number | null;
  worst: number | null;
  /** The largest peak-to-trough fall of the running net over closed trades in exit order, positive. */
  drawdown: number;
  /** The running net after each closed trade, in exit order. */
  curve: Array<{ x: string; y: number }>;
}

export interface AutomationsPerformance {
  dbPresent: boolean;
  arms: Record<string, AutomationsLine>;
  /** Every arm stacked as sized. */
  combined: AutomationsLine;
}

/** An automation's research verdict, item 75's register label (`passed`, `failed`, `no_verdict`). */
export type AutomationVerdict = "passed" | "failed" | "no_verdict" | null;

export interface AutomationGroupMember {
  name: string;
  enabled: boolean;
  verdict: AutomationVerdict;
}

/** A set of like automations switched as one (`groups` in the automations config). */
export interface AutomationGroup {
  name: string;
  enabled: boolean;
  automations: AutomationGroupMember[];
}

/** What lets an automation enter, or the one thing that stops it (`automations.groupedit`): `on` (its own
 *  switch on, its group on or none, its start come), `waiting` (all but the start, which is `from`),
 *  `off_group` (its own switch on, its group off), `off_own` (its own switch off). */
export type AutomationState = "on" | "waiting" | "off_group" | "off_own";

/** One automation on the Groups pages: its OWN switch beside the state it is in, its own start, its
 *  research verdict, its underlying, whether it ships with the module and whether it has a forward rule. */
export interface GroupsEditMember {
  name: string;
  enabled: boolean;
  start: string | null;
  state: AutomationState;
  /** The date a `waiting` automation starts. */
  from: string | null;
  verdict: AutomationVerdict;
  symbol: string | null;
  shipped: boolean;
  forward: boolean;
}

/** A group's spec as the module's `groups` block holds it (`config.groups`). */
export interface GroupSpec {
  enabled: boolean;
  start?: string;
  allocation?: number;
  max_risk?: number;
  /** Each member's percent of the allocation and the cap, at most 100 in all. */
  shares?: Record<string, number>;
}

export interface GroupsEditGroup extends GroupSpec {
  name: string;
  members: GroupsEditMember[];
}

/** `GET /api/automations/groups/edit`: every group and every automation in none (`cli groups-view`). */
export interface GroupsEditView {
  today: string;
  /** The session a switch made today starts from. */
  next_session: string;
  counts: Record<AutomationState, number>;
  groups: GroupsEditGroup[];
  ungrouped: GroupsEditMember[];
}

/** A change to the groups: every group that will have members, each spec whole, the automations that
 *  move (`null`: into no group) and the automations whose own switch flips. */
export interface GroupsChange {
  groups: Record<string, GroupSpec>;
  members: Record<string, string | null>;
  switches: Record<string, boolean>;
}

/** `POST /api/automations/groups/check`: the module's problems, what a save would write, and the view as
 *  it would be after it. */
export interface GroupsCheckResult {
  ok: boolean;
  error?: string;
  problems?: string[];
  moves?: Record<string, string | null>;
  switches?: Record<string, { enabled: boolean; start?: string }>;
  notes?: string[];
  after?: GroupsEditView | null;
}

/** `GET /api/automations/groups`: every group, or the reason the package could not be asked. */
export interface AutomationGroupsResult {
  groups: AutomationGroup[];
  error: string | null;
}

/** A block's backtest label: judged as it stands, changed since, or never judged (`enrolment.labels`). */
export type BuilderBlockState = "backtested" | "changed" | "not_backtested";

export interface BuilderBlock {
  block: string;
  state: BuilderBlockState;
  /** The automation's own keys in this block, as its config resolves them. */
  keys: Record<string, unknown>;
}

/** One data need of a backtest and whether our stores cover it (`enrolment.suggestion`): our own
 *  first, a licensed (vendor) store only where they fall short and the licence allows new work. */
export interface BuilderNeed {
  field: string;
  expiry?: string;
  cadence: string;
  covered: boolean;
  source: string | null;
  /** The covering store is the vendor's: the run's result is licensed. */
  licensed: boolean;
  nearest?: { source: string; first: string | null; last: string | null; why: string | null };
  /** A covered need: the last session its store holds, where the run ends when that is before the window's. */
  through?: string;
  /** Why no store is even a candidate (none holds this expiry class at this cadence). */
  why?: string;
  /** A need for every expiry our stores miss, which they would cover for monthlies only. */
  monthly?: { source: string; first: string | null; through: string | null };
}

/** A window a Builder run may take: a span one store holds for every need (`enrolment.windows`). */
export interface BuilderWindow {
  /** What a run names; the orchestrator refuses one the module does not offer. */
  key: string;
  start: string;
  end: string;
  source: string;
  licensed: boolean;
  /** The chain store's share of the trading days in the window, when it is counted. */
  density: number | null;
  /** Under the registry's dense mark: Dolt's early years, about three days a week. */
  thin: boolean;
  /** What the store holds, in the registry's words. */
  holds: string;
}

export interface BuilderSuggestion {
  blocks: string[];
  window: [string, string];
  needs: BuilderNeed[];
  possible: boolean;
  /** Some need is covered by a licensed store. */
  licensed: boolean;
  /** One of the full set's own seven underlyings; another can be added to it. */
  full_set?: boolean;
  /** Every window our stores can replay, our own first. Absent from an older module. */
  windows?: BuilderWindow[];
}

export interface BuilderAutomation {
  name: string;
  group: string | null;
  symbol: string;
  enabled: boolean;
  note: string | null;
  start: string | null;
  register: { item: number; verdict: string } | null;
  summary: string;
  blocks: BuilderBlock[];
  forward: Record<string, unknown> | null;
  suggestion: BuilderSuggestion | null;
  /** The Builder run pinned as its backtest reference (`builder-<name>-<YYMMDDHHMM>`), or none. */
  reference: string | null;
}

/** `POST /api/automations/builder/reference`: the pin written (`run` null: unpinned). */
export interface BuilderReferenceResult {
  ok: boolean;
  name?: string;
  run?: string | null;
  error?: string;
  problems?: string[];
}

/** `GET /api/automations/builder`: every automation block by block, or why the package could not answer. */
export interface BuilderResult {
  date: string | null;
  automations: BuilderAutomation[];
  error: string | null;
}

/**
 * `POST /api/automations/builder/draft`: the module's check of one automation, or (asked by name alone)
 * the automation as the editor starts from it. `problems` empty means it can be saved. `view` is the
 * Builder row the save would show, in the CLI's shape (block labels included). Writes nothing.
 */
export interface BuilderDraftResult {
  ok: boolean;
  name?: string;
  automation?: Record<string, unknown> | null;
  problems?: string[];
  view?: Record<string, unknown> | null;
  new?: boolean;
  error?: string;
}

/** `POST /api/automations/builder/save`: the entry written, always off, or why nothing was. */
export interface BuilderSaveResult {
  ok: boolean;
  name?: string;
  added?: boolean;
  enabled?: false;
  backup?: string | null;
  problems?: string[];
  error?: string;
  code?: string;
}

/** `POST /api/automations/builder/backtest`: the explore run queued, or why it was not. */
export interface BuilderBacktestResult {
  ok: boolean;
  name?: string;
  run?: string;
  task?: number | null;
  window?: [string, string];
  source?: string | null;
  /** The window key the run took, or null for the last year. */
  key?: string | null;
  error?: string;
  code?: string;
}

/** One queue row of a Builder backtest. */
export interface BuilderRunTask {
  id: number | null;
  run: string;
  status: string;
  queued_at: string | null;
  started_at: string | null;
  finished_at: string | null;
  exit_code: number | null;
  note: string | null;
}

/** One arm's figures as the run wrote them. */
export interface BuilderRunArm {
  sessions: number | null;
  trades: number | null;
  net: number | null;
  mean_net: number | null;
}

/** One finished Builder backtest, in brief. */
export interface BuilderRunResult {
  run: string;
  symbol: string;
  source: string;
  licensed: boolean;
  cadence: string | null;
  sessions: [string, string] | null;
  session_count: number | null;
  summary: Record<string, BuilderRunArm>;
  still_open: number | null;
  generated_at: string | null;
  /** The cumulative net by closed trade, thinned for a sparkline. */
  spark: number[];
  net: number;
  /** What this run's stored automation changed from the run before. */
  changes: BuilderRunChange[];
}

/** One key that differs between two runs' stored automations. */
export interface BuilderRunChange {
  key: string;
  before: unknown;
  after: unknown;
}

/** One year (or All) of a run, by exit session: gross - fees - settlement - slippage = net. */
export interface BuilderRunYear {
  period: string;
  trades: number;
  gross: number;
  fees: number;
  settlement: number;
  slippage: number;
  net: number;
  /** Gross - fees - settlement at mid, before slippage. */
  net_mid?: number;
  max_dd: number;
}

/** One exit quarter of a run; `counted` once it has enough sessions to count. */
export interface BuilderRunQuarter {
  quarter: string;
  trades: number;
  sessions: number;
  net: number;
  counted: boolean;
}

/**
 * The study kit's robustness reads over one run's trades (`automationcards.robustness_reads`):
 * in-sample, one run, never a verdict.
 */
export interface BuilderRunRobustness {
  best_sessions:
    | { k: number; n: number; insufficient: true }
    | {
        k: number;
        n: number;
        sessions: string[];
        best_net: number;
        total_net: number;
        /** The best sessions' part of a positive total; null when the total is zero or below. */
        share: number | null;
        rest_mean: number;
        rests_on_best: boolean;
      };
  /** Mean net per trade with fees and slippage scaled; settlement does not scale. */
  cost_stress: { rows: Array<{ multiplier: number; mean_net: number | null }>; pass_stress: number; survives: boolean } | null;
  /** Losses as positive dollars. Under the evidence floor only `insufficient` and the counts. */
  drawdown: {
    actual: number;
    insufficient?: true;
    evidence_trades: number;
    min_evidence?: number;
    horizon?: number;
    resamples?: number;
    drawdown_reduce?: number;
    drawdown_block?: number;
    streak_reduce?: number;
    streak_block?: number;
  };
  quarters: { rows: BuilderRunQuarter[]; min_sessions: number; counted: number; profitable: number };
}

/** One closed trade as the run recorded it. */
export interface BuilderRunTrade {
  session: string | null;
  entry_minute: string | number | null;
  exit_session: string | null;
  exit_minute: string | number | null;
  exit_kind: string | null;
  expiry: string | null;
  legs: Array<{ name?: string; right?: string; side?: number; strike?: number }>;
  quantity: number | null;
  entry_mid: number | null;
  exit_mid: number | null;
  gross: number;
  fees: number;
  settlement: number;
  slippage: number;
  net: number;
}

/** `GET /api/automations/builder/run?name=&run=`: one run's whole card. */
export interface BuilderRunCard {
  run: string;
  automation: string;
  symbol: string;
  source: string;
  store: string | null;
  cadence: string | null;
  sessions: [string, string] | null;
  session_count: number | null;
  still_open: number | null;
  generated_at: string | null;
  licensed: boolean;
  tiles: {
    net: number;
    /** The same trades at mid: gross - fees - settlement, before slippage (P&L + slippage). */
    net_mid?: number;
    gross: number;
    trades: number;
    sessions: number | null;
    per_trade: number | null;
    win_rate: number | null;
    max_dd: number;
    net_over_dd: number | null;
  };
  curve: Array<{ i: number; session: string | null; net: number; gross: number; dd: number }>;
  years: BuilderRunYear[];
  /** Absent on a card read by an older backtest package. */
  robustness?: BuilderRunRobustness;
  skips: Array<{ reason: string; sessions: number }>;
  previous: string | null;
  changes: BuilderRunChange[];
  trades: BuilderRunTrade[];
}

/** `GET /api/automations/builder/runs?name=`: the explore-runs list. */
export interface BuilderRuns {
  name: string;
  tasks: BuilderRunTask[];
  runs: BuilderRunResult[];
  /** Licensed results left out while the licensed-results gate is shut. */
  withheld: number;
  results_unavailable: boolean;
}

/** The idea search (#1294): entry filters for one saved automation, scored on the part of one Builder
 *  run before its split. Hypotheses only; a surviving idea is declared, then tested once. */
export type IdeaGate = "loses" | "beats_menu" | "plateau" | "stable" | "adds" | "pays_at_1_5x";

export interface IdeaHeldOutRead {
  idea: string;
  at: string;
  use: number;
  sessions: number;
  skipped: number;
  skipped_mean: number | null;
  uplift: number;
  ci: [number, number] | null;
  verdict: "passed" | "failed" | "no_verdict";
}

export interface IdeaRow {
  id: string;
  idea: string;
  conditions: string[];
  skipped: number;
  skipped_share: number;
  skipped_mean: number | null;
  skipped_ci: [number, number] | null;
  uplift: number;
  uplift_shrunk: number;
  z: number;
  p: number;
  p_menu: number;
  holm: boolean;
  bh: boolean;
  plateau: { verdict: "plateau" | "lone_peak" | "no_neighbours"; axes: Array<{ condition: string; verdict: string }> };
  stability: { folds_negative: number; stable: boolean };
  adds: { adds: boolean } | null;
  cost: { mean_by_multiplier: Record<string, number | null>; survives: boolean } | null;
  held_out: { planned_sessions: number; mde: number | null; testable: boolean };
  gates: Record<IdeaGate, boolean>;
  survives: boolean;
  /** The register row drafted when it was declared, or null. */
  declaration: Record<string, unknown> | null;
  /** Its one held-out test, once read. */
  held_out_read: IdeaHeldOutRead | null;
}

export interface IdeaSearchBrief {
  name: string;
  automation: string;
  run: string;
  symbol: string;
  source: string | null;
  sessions: [string, string];
  split: string;
  base: { sessions: number; trades: number; net: number; mean_session: number; held_out_sessions: number };
  tried: number;
  survivors: number;
  holm_survivors: number;
  bh_survivors: number;
  generated_at: string;
  licensed: boolean;
  declared: string[];
  held_out_used: number;
}

export interface IdeaSearch extends IdeaSearchBrief {
  draws: number;
  null_min_median: number | null;
  rules: Record<string, number>;
  rows: IdeaRow[];
  held_out_uses: IdeaHeldOutRead[];
}

export interface IdeaSearchTask {
  id: number | null;
  search: string;
  status: string;
  queued_at: string | null;
  finished_at: string | null;
  note: string | null;
}

export interface IdeaSearches {
  name: string;
  tasks: IdeaSearchTask[];
  searches: IdeaSearchBrief[];
  withheld: number;
  results_unavailable: boolean;
}

/** The settings grid (#1294): exit and width settings for one saved automation, each a replay beside
 *  the automation's own over one Builder run's span, scored on the part before its split. Hypotheses
 *  only; a surviving setting is declared, then tested once. */
export type GridGate = "gains" | "beats_menu" | "plateau" | "stable" | "pays_at_1_5x";

export interface GridHeldOutRead {
  setting: string;
  at: string;
  use: number;
  sessions: number;
  moved: number;
  mean_change: number | null;
  gain: number;
  ci: [number, number] | null;
  verdict: "passed" | "failed" | "no_verdict";
}

export interface GridRow {
  id: string;
  axis: string;
  setting: string;
  moved: number;
  /** False for a setting that changed too few sessions to be scored (`why`). */
  scored: boolean;
  why?: string;
  mean_change?: number | null;
  change_ci?: [number, number] | null;
  gain?: number;
  gain_shrunk?: number;
  z?: number;
  p?: number;
  p_menu?: number;
  holm?: boolean;
  bh?: boolean;
  plateau?: { verdict: "plateau" | "lone_peak" | "no_neighbours" | "unmeasured" };
  stability?: { folds_positive: number; stable: boolean };
  cost?: { mean_change_at_1_5x: number | null };
  held_out?: { planned_sessions: number; mde: number | null; testable: boolean };
  gates?: Record<GridGate, boolean>;
  survives: boolean;
  declaration: Record<string, unknown> | null;
  held_out_read: GridHeldOutRead | null;
}

export interface GridBrief {
  name: string;
  automation: string;
  run: string;
  symbol: string;
  source: string | null;
  sessions: [string, string];
  split: string;
  base: { sessions: number; trades: number; net: number; held_out_sessions: number };
  tried: number;
  survivors: number;
  holm_survivors: number;
  bh_survivors: number;
  /** Whether replaying the automation gave the run's own trades again. */
  base_matches_run: boolean;
  generated_at: string;
  licensed: boolean;
  declared: string[];
  held_out_used: number;
}

export interface GridResult extends GridBrief {
  draws: number;
  null_max_median: number | null;
  /** Settings the module's check refused, each with its first problem; never replayed. */
  refused: Record<string, string>;
  rows: GridRow[];
  held_out_uses: GridHeldOutRead[];
}

export interface GridTask {
  id: number | null;
  grid: string;
  status: string;
  queued_at: string | null;
  finished_at: string | null;
  note: string | null;
}

export interface Grids {
  name: string;
  tasks: GridTask[];
  grids: GridBrief[];
  withheld: number;
  results_unavailable: boolean;
}

export interface GridActionResult {
  ok: boolean;
  error?: string;
  code?: string;
  grid?: string;
  task?: number | null;
  split?: string;
  row?: Record<string, unknown>;
  verdict?: GridHeldOutRead["verdict"];
}

export interface IdeaActionResult {
  ok: boolean;
  error?: string;
  code?: string;
  search?: string;
  task?: number | null;
  split?: string;
  row?: Record<string, unknown>;
  verdict?: IdeaHeldOutRead["verdict"];
}

/** The 10th, 50th and 90th percentile of the running net after `k` trades of the pinned run. */
export interface ReferenceBandPoint {
  k: number;
  p10: number;
  p50: number;
  p90: number;
}

/** An automation's backtest reference, from its pinned Builder run (`backtest read automation-reading`). */
export interface AutomationReference {
  run: string;
  automation: string;
  symbol: string | null;
  first: string | null;
  last: string | null;
  trades: number;
  sessions: number | null;
  /** Each last reason's count over the run's sessions (`entered` among them). */
  reasons?: Record<string, number>;
  insufficient: boolean;
  min_trades?: number;
  mean?: number;
  sd?: number;
  /** p10, p25, p50, p75, p90 of a trade's net: the usual range is p10 to p90. */
  percentiles?: Record<string, number>;
  entry_rate?: number | null;
  missed?: Record<string, number>;
  exits?: Record<string, number>;
  band?: ReferenceBandPoint[];
  sample_computed?: number | null;
}

export type ReadingPlace = "below" | "inside" | "above";

/** One paper trade against the reference. */
export interface ReadingRow {
  i: number;
  session: string | null;
  net: number;
  place: ReadingPlace;
  /** Where the trade falls among the run's trades, 0 to 100. */
  percentile: number;
  running: number;
}

/**
 * `GET /api/automations/automation/reading?name=`: the automation's paper record set against its pinned
 * Builder run, computed by the backtest package. `pinned` null: no run pinned. `withheld`: the run is
 * licensed and licensed results are not shown now.
 */
export interface AutomationReading {
  name: string;
  pinned: string | null;
  withheld: boolean;
  error: string | null;
  reading: {
    state: "no_reference" | "no_trades" | "too_few" | ReadingPlace;
    licensed: boolean;
    reference: AutomationReference;
    paper: {
      trades: number;
      rows?: ReadingRow[];
      counts?: Record<ReadingPlace, number>;
      net?: number;
      mean?: number | null;
      running_place?: ReadingPlace | null;
      band_at?: ReferenceBandPoint | null;
      entry: { entered: number; missed: number; rate: number | null };
    };
    sample?: { needed: number | null; basis: "declared" | "computed" | null; have: number };
  } | null;
}

/** A step of the automation's decision, in the order the engine takes them (`automations.entries`). */
export type EntryStep = "due" | "data" | "trigger" | "limits" | "gates" | "structure" | "size";

export interface EntryCause {
  reason: string;
  count: number;
  share: number | null;
}

/** One step: the sessions that reached it, those whose last try stopped there by cause, and those past it. */
export interface EntryFunnelStep {
  step: EntryStep;
  label: string;
  reached: number;
  reached_share: number | null;
  stopped: number;
  /** The sessions past it: the last step's is what entered. */
  passed: number;
  passed_share: number | null;
  causes: EntryCause[];
}

/** The sessions an automation was due, decided ones only; `pending` its windows still open. */
export interface EntryFunnel {
  due: number;
  pending: number;
  entered: number;
  missed: number;
  rate: number | null;
  steps: EntryFunnelStep[];
  /** The step that lost the most sessions (the earlier on a tie); null when none did. */
  most_lost: { step: EntryStep; label: string; stopped: number; share: number | null } | null;
}

/** `GET /api/automations/entries`: every automation's paper funnel (`automations cli entries`). */
export interface AutomationsEntriesRow {
  name: string;
  group: string | null;
  symbol: string | null;
  enabled: boolean;
  /** False for one the register names that the config no longer does. */
  configured: boolean;
  first: string | null;
  last: string | null;
  funnel: EntryFunnel;
}

export interface AutomationsEntries {
  automations: AutomationsEntriesRow[];
  error: string | null;
}

/** One cause beside the pinned run's: its share of paper's due sessions and of the run's. */
export interface EntryCauseRow {
  reason: string;
  step: EntryStep;
  paper: number | null;
  run: number | null;
}

export interface EntryDay {
  session: string;
  outcome: "entered" | "missed" | "pending";
  reason: string | null;
  step: EntryStep | null;
}

/**
 * `GET /api/automations/automation/entries?name=`: one automation's funnel from its session register,
 * and its pinned run's through the same steps beside it (both computed by the automations package).
 * `run` is null with nothing pinned or the run withheld (licensed while licensed results are hidden).
 */
export interface AutomationEntries {
  name: string;
  pinned: string | null;
  withheld: boolean;
  error: string | null;
  reference: { run: string; licensed: boolean; first: string | null; last: string | null; sessions: number | null } | null;
  first: string | null;
  last: string | null;
  paper: EntryFunnel | null;
  run: EntryFunnel | null;
  causes: EntryCauseRow[];
  days: EntryDay[];
}

/** A pair of members' net per session, correlated two ways (`core.portfolio`). */
export interface GroupPair {
  a: string;
  b: string;
  /** Every session inside both spans, one that closed nothing counted as zero: do they lose together. */
  zero_filled: number | null;
  zero_filled_periods: number;
  /** Only sessions both closed something: when both trade, do they move together. */
  similarity: number | null;
  similarity_periods: number;
  thin: boolean;
}

/** One group read as a unit (`automations cli groups-read`). */
export interface AutomationsGroupUnit {
  name: string;
  enabled: boolean;
  allocation: number | null;
  max_risk: number | null;
  shares: Record<string, number>;
  members: string[];
  /** Its members' closed trades stacked as sized, in the performance page's line. */
  line: AutomationsLine;
  /** Net and drawdown as shares of the allocation; null without one. */
  against_allocation: { net: number | null; drawdown: number | null } | null;
  /** The most risk held at once (session grain, an upper bound) and its share of the cap. */
  risk: { peak: number; session: string; cap: number | null; of_cap: number | null } | null;
  member_risk: Array<{ name: string; share: number | null; peak: number | null; session: string | null; limit: number | null; of_limit: number | null }>;
  /** Sessions missed with the group's cap or a member's share as the last reason. */
  refused: { cap: number; share: number; unknown: number; latest: Array<{ session: string; automation: string; by: "cap" | "share" | "unknown" }> };
  together: {
    pairs: GroupPair[];
    effective_members: number | null;
    members_counted: string[];
    shared_periods: number;
    thin: boolean;
    drawdown: { combined: number; sum_of_arms: number; benefit: number | null };
    min_periods: number;
  } | null;
}

/** `GET /api/automations/groups/read`: every group as a unit, and the automations in none as one line. */
export interface AutomationsGroupsRead {
  groups: AutomationsGroupUnit[];
  ungrouped: { members: string[]; line: AutomationsLine };
  error: string | null;
}

/** A stored run arm whose settings are a module arm's. */
export interface ArmRunMatch {
  run: string;
  /** What the study called the arm: a match is by settings, never by name. */
  run_arm: string;
  window: string;
  first: string | null;
  last: string | null;
  sessions: number;
}

/**
 * `GET /api/arm/reading?module=&arm=`: a module arm's paper record per session against the stored run
 * whose arm has its settings (`backtest read arm-reading`). `matches` empty: no run has tried them.
 * `withheld`: the run read the vendor's store and licensed results are hidden now.
 */
export interface ArmBacktestReading {
  module: string;
  arm: string;
  unit: "session";
  matches: ArmRunMatch[];
  chosen: ArmRunMatch | null;
  /** Where the paper record starts: the module's current measurement era. */
  paper_from: string | null;
  withheld: boolean;
  error: string | null;
  reading: NonNullable<AutomationReading["reading"]> | null;
}

