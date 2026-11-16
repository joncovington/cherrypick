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

/** One data need of a backtest and whether our own stores cover it (`enrolment.suggestion`). */
export interface BuilderNeed {
  field: string;
  expiry?: string;
  cadence: string;
  covered: boolean;
  source: string | null;
  nearest?: { source: string; first: string | null; last: string | null; why: string | null };
}

export interface BuilderSuggestion {
  blocks: string[];
  window: [string, string];
  needs: BuilderNeed[];
  possible: boolean;
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
}

/** `GET /api/automations/builder`: every automation block by block, or why the package could not answer. */
export interface BuilderResult {
  date: string | null;
  automations: BuilderAutomation[];
  error: string | null;
}
