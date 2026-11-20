import type { MeasurementBreak, TradeMoney, TradeTotals } from "./modules.js";

/**
 * The ladder's read surface (packages/ladder): a paper-only share dip ladder on leveraged index
 * funds. A position is one LOT, bought once at a limit under the previous close and sold once at its
 * target; each arm keeps its own book. Money is the module's own, whole-position signed cash flow
 * (`entry_value` the purchase, `exit_value` the sale), so a lot adds up as it is stored. The
 * per-arm summary and the next buy levels are the module's own (`ladder summary` / `ladder status`)
 * through `services/ladderBridge.ts`; `readers/ladder.ts` reads the ledger for the lots.
 */

/** The group's "hospital": an open lot older than this many calendar days. Mirrors the module's
 *  `analytics.HOSPITAL_DAYS`, which counts the lots in the summary; a test pins the two together. */
export const LADDER_HOSPITAL_DAYS = 18;

/** A lot still held, with the bid the loop last marked it at. */
export interface LadderOpenLot {
  positionId: string;
  arm: string;
  symbol: string;
  quantity: number;
  buyPrice: number;
  /** The session the lot was bought in, and the time (ISO, with the ET offset the module wrote). */
  buySession: string;
  buyTime: string;
  /** Whole-position cash paid, negative. */
  entryValue: number;
  targetPrice: number | null;
  /** `gain`, or for the `decay` target `breakeven` / `floor` once it has stepped down. */
  targetStage: string | null;
  lastBid: number | null;
  lastMarkTime: string | null;
  /** quantity x last bid, whole position; null before the first mark. */
  valueAtBid: number | null;
  /** Value at the bid less the cost, before fees on the sale; null before the first mark. */
  unrealisedAtBid: number | null;
  /** Calendar days from the buy session to today (ET). */
  daysHeld: number;
  /** Held past `LADDER_HOSPITAL_DAYS`. */
  hospital: boolean;
}

/** A sold lot in the trade table standard: entry (the purchase, -) + exit (the sale, +) = gross. */
export interface LadderHistoryRow extends TradeMoney {
  positionId: string;
  arm: string;
  symbol: string;
  buySession: string;
  buyTime: string;
  buyPrice: number;
  /** The level the rule asked for; the fill can be under it only on a gap. */
  buyLevel: number;
  refClose: number | null;
  sellSession: string | null;
  sellTime: string | null;
  sellPrice: number | null;
  targetPrice: number | null;
  /** `target_gain`, `target_breakeven` or `target_floor`. */
  exitReason: string | null;
  holdDays: number | null;
  /** Net over the cost of the lot, as a percentage the way the module stores it (4.01 = +4.01%). */
  netPct: number | null;
}

export interface LadderHistory {
  rows: LadderHistoryRow[];
  totals: TradeTotals;
}

export interface LadderPayload {
  /** The session the loop last ran in. */
  session: string | null;
  dbPresent: boolean;
  /** Today's date in New York, the day `daysHeld` counts to. */
  today: string;
  openLots: LadderOpenLot[];
  measurementBreaks: MeasurementBreak[];
  lastIteration: { ranAt: number; phase: string; status: string; ageSeconds: number } | null;
}

/** One arm as `ladder status` names it: how it enters and how it sets the target. */
export interface LadderArmRule {
  entry: string;
  target: string;
}

/** One fund in `ladder status`: the latest quote, the previous close and each arm's next buy level. */
export interface LadderFund {
  bid: number | null;
  ask: number | null;
  refClose: number | null;
  /** Arm to the level its next lot would be bought at; null where it has none (a cap, no close). */
  nextBuy: Record<string, number | null>;
}

/** `ladder status`: files only, no broker. */
export interface LadderStatus {
  ok: boolean;
  error: string | null;
  date: string | null;
  hours: string | null;
  inHours: boolean | null;
  arms: Record<string, LadderArmRule>;
  funds: Record<string, LadderFund>;
}

/** One arm in `ladder summary`, in the group's terms as well as the ledger's. */
export interface LadderArmSummary {
  lots: number;
  closed: number;
  open: number;
  /** Realised, after fees: the sum of the sold lots' net. */
  netPnl: number;
  grossPnl: number;
  fees: number;
  /** Open lots valued at the last bid, less their cost. */
  unrealisedAtBid: number;
  soldWithin18d: number;
  inHospital: number;
  medianHoldDays: number | null;
  longestClosedHoldDays: number | null;
  oldestOpenDays: number | null;
  symbols: string[];
}

export interface LadderSummary {
  ok: boolean;
  error: string | null;
  arms: Record<string, LadderArmSummary>;
}
