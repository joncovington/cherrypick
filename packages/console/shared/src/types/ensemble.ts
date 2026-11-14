import type { MeasurementBreak, TradeMoney, TradeTotals } from "./modules.js";

/**
 * ensemble's read surface (packages/ensemble): research item 75's arms in paper. An arm is one
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
export interface EnsembleLeg {
  role: string;
  right: string;
  side: number;
  strike: number;
}

/** The latest usable mark of an open structure. */
export interface EnsembleMark {
  ts: string;
  /** What closing would bring at the mid, whole position, signed. */
  value: number | null;
  spot: number | null;
}

export interface EnsemblePosition {
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
  legs: EnsembleLeg[];
  /** Latest usable mark of an open position, with the newest spot any mark recorded. */
  mark: EnsembleMark | null;
  /** entryValue + mark.value: what the position is worth now before any cost. Open positions only. */
  unrealised: number | null;
}

/** The gate that stopped an arm on a tick: the engine's first failing check and the value it read. */
export interface EnsembleGate {
  reason: string;
  check: string | null;
  value: string | null;
  /** How many ticks it refused the same way today, from the decision journal. */
  occurrences: number | null;
}

/** One arm's session as the register records it. */
export interface EnsembleEntryRow {
  arm: string;
  symbol: string;
  entrySession: string;
  /** pending (its window is not over), entered, or missed (the window closed unacted). */
  outcome: string;
  /** The last reason a tick in the window gave. */
  refusal: string | null;
  positionId: string | null;
  decidedAt: string | null;
  gate: EnsembleGate | null;
}

export interface EnsemblePayload {
  /** The loop's last session, else the newest session the register names. */
  session: string | null;
  dbPresent: boolean;
  /** Every arm the ledger names, sorted. */
  arms: string[];
  positions: EnsemblePosition[];
  /** The register's rows for `session`: each arm that was due, and how it stands. */
  today: EnsembleEntryRow[];
  measurementBreaks: MeasurementBreak[];
  lastIteration: { ranAt: number; phase: string; status: string; ageSeconds: number } | null;
}

/** A closed structure in the trade table standard: entry + exit = gross; gross - fees - settlement - slippage = net. */
export interface EnsembleHistoryRow extends TradeMoney {
  positionId: string;
  arm: string;
  symbol: string;
  expiration: string;
  entrySession: string;
  exitSession: string | null;
  exitReason: string | null;
  netStress: number | null;
  netMid: number | null;
  legs: EnsembleLeg[];
}

export interface EnsembleHistory {
  rows: EnsembleHistoryRow[];
  totals: TradeTotals;
}

/** One arm's (or all arms') closed trades, net and drawdown. Dollars, whole position. */
export interface EnsembleLine {
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

export interface EnsemblePerformance {
  dbPresent: boolean;
  arms: Record<string, EnsembleLine>;
  /** Every arm stacked as sized. */
  combined: EnsembleLine;
}
