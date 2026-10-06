/**
 * The shapes every module's costs page reads (curve 2026-10-05, pmcc 2026-10-06), so the page is one
 * component and the money is one arithmetic (`readers/armCosts.ts`).
 */

/**
 * One arm's CLOSED positions in the suite's money layout (root CLAUDE.md): gross, then trading
 * fees, settlement and slippage as separate costs, so gross - fees - settlement - slippage = net.
 */
export interface ArmMoneyRow {
  arm: string;
  positions: number;
  wins: number;
  /** Premium received, whole-position dollars -- what fee drag is measured against. */
  premium: number;
  grossPnl: number;
  /** Trading fees only -- commissions and pass-throughs. */
  fees: number;
  settlementFees: number;
  slippage: number;
  netPnl: number;
  winRate: number | null;
}

/** One arm's entry sessions, counted per SESSION not per tick: entered if any tick filled,
 *  otherwise the session's last refusal. A gate that refused 400 ticks is one session. */
export interface EntryOutcomes {
  arm: string;
  sessions: number;
  entered: number;
  /** reason -> sessions whose last refusal it was. */
  refusals: Record<string, number>;
}

/** A module's costs read. `since` is the latest measurement break (or era start), null for all time. */
export interface ArmCostsPayload {
  arms: ArmMoneyRow[];
  since: string | null;
  entryOutcomes: EntryOutcomes[];
  entryOutcomesAll: EntryOutcomes[];
}
