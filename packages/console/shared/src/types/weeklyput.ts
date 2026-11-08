import type { MeasurementBreak, TradeMoney, TradeTotals } from "./modules.js";

/**
 * weeklyput's read surface (packages/weeklyput): item 57's unhedged short put in paper, one put a
 * name each non-monthly weekly expiry, held to the expiry's posted close. A position is one short
 * put; the unit the module is judged on is the CYCLE (one expiry), the equal-weight mean of net in
 * bp over the names that traded it. That mean is the module's own (`weeklyput cycles` / `summary`);
 * the console reads it and never re-derives it. Python field names are re-spelled once, in
 * `readers/weeklyput.ts` and `services/weeklyputBridge.ts`.
 */

/** One short put, with the latest mark the loop took of it. Money is whole-position, signed cash flow. */
export interface WeeklyputPosition {
  positionId: string;
  arm: string;
  symbol: string;
  status: string;
  expiration: string;
  entrySession: string;
  entryTime: string | null;
  strike: number;
  quantity: number;
  /** SPX settles in cash; an ETF put is delivered. */
  settlementStyle: string;
  entrySpot: number | null;
  entryForward: number;
  entryDelta: number | null;
  entryBid: number | null;
  entryAsk: number | null;
  entryMid: number | null;
  /** The credit taken in, whole position, +. */
  entryValue: number;
  entryFees: number;
  entrySlippage: number;
  notional: number;
  settleSession: string | null;
  settlePrice: number | null;
  exitValue: number | null;
  exitReason: string | null;
  settlement: number | null;
  grossPnl: number | null;
  fees: number | null;
  slippage: number | null;
  netPnl: number | null;
  netBp: number | null;
  assignmentExposedAt: string | null;
  /** The latest usable mark of an open put. */
  mark: WeeklyputMark | null;
}

export interface WeeklyputMark {
  ts: string;
  mid: number | null;
  /** Signed cash flow to close at mid (a debit, so −). */
  value: number | null;
  spot: number | null;
  extrinsic: number | null;
}

/** One row of the cycle register: a name's outcome for one expiry. */
export interface WeeklyputEntryRow {
  arm: string;
  symbol: string;
  expiration: string;
  entrySession: string;
  /** pending (window not over), entered, or missed (the window closed unacted). */
  outcome: string;
  refusal: string | null;
  positionId: string | null;
  decidedAt: string | null;
}

export interface WeeklyputPayload {
  session: string | null;
  dbPresent: boolean;
  positions: WeeklyputPosition[];
  entries: WeeklyputEntryRow[];
  measurementBreaks: MeasurementBreak[];
  lastIteration: { ranAt: number; phase: string; status: string; ageSeconds: number } | null;
}

/** `weeklyput status`: the calendar, from the module's own clock. */
export interface WeeklyputStatus {
  ok: boolean;
  error: string | null;
  date: string | null;
  decisionWindow: string | null;
  /** The expiry whose entry session is today; null on any other day. */
  entryToday: string | null;
  nextCycle: string | null;
  nextEntrySession: string | null;
  arms: string[];
  names: string[];
  /** Open puts whose expiry has passed and whose posted close is not in yet. */
  awaitingClose: string[];
}

export interface WeeklyputCycleName {
  status: string;
  strike: number;
  entryDelta: number | null;
  entryValue: number;
  exitValue: number | null;
  grossPnl: number | null;
  fees: number | null;
  settlement: number | null;
  slippage: number | null;
  netPnl: number | null;
  netBp: number | null;
  exitReason: string | null;
  assignmentExposed: boolean;
}

/** One (arm, expiry), as `weeklyput cycles` returns it. */
export interface WeeklyputCycle {
  arm: string;
  expiration: string;
  entrySession: string;
  names: Record<string, WeeklyputCycleName>;
  /** Names not entered: still pending, or missed with the last refusal. */
  unentered: Record<string, { outcome: string; refusal: string | null }>;
  finished: boolean;
  /** The equal-weight mean of the names' net bp, null until every put has settled. */
  cycleNetBp: number | null;
  netPnl: number | null;
}

export interface WeeklyputCycles {
  ok: boolean;
  error: string | null;
  cycles: WeeklyputCycle[];
}

export interface WeeklyputArmSummary {
  cycles: number;
  meanBp: number | null;
  stdevBp: number | null;
  positiveShare: number | null;
  worst: { expiration: string; cycleNetBp: number } | null;
  byName: Record<string, { trades: number; meanBp: number }>;
  money: {
    entryValue: number;
    exitValue: number;
    fees: number;
    settlement: number;
    slippage: number;
    grossPnl: number;
    netPnl: number;
    /** (fees + settlement) over the premium collected. */
    feeDrag: number | null;
  };
  missed: number;
}

export interface WeeklyputSummary {
  ok: boolean;
  error: string | null;
  arms: Record<string, WeeklyputArmSummary>;
}

/** A settled put in the trade table standard: entry (the credit) + exit (minus the intrinsic paid) = gross. */
export interface WeeklyputHistoryRow extends TradeMoney {
  positionId: string;
  arm: string;
  symbol: string;
  expiration: string;
  entrySession: string;
  settleSession: string | null;
  strike: number;
  entryDelta: number | null;
  settlePrice: number | null;
  netBp: number | null;
  exitReason: string | null;
}

export interface WeeklyputHistory {
  rows: WeeklyputHistoryRow[];
  totals: TradeTotals;
}
