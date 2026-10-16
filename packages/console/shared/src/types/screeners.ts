/**
 * The income screener lists (2026-10-16): short puts, covered calls and credit spreads, one row
 * per name, as the evening capture saved them (`market-report/vendor-screeners/<date>/`). Each row
 * carries the listed trade, the strike our reproduced rule picks for it (`packages/technicals`
 * `score-screeners`), whether an earnings date falls inside the user's own cycle, and the regular-
 * hours liquidity readings of the listed option and of the cycle monthly the user would trade
 * instead (`fetch_screener_greeks.py measure`). The source is never named.
 */

export type ScreenerList = "short-puts" | "covered-calls" | "credit-spreads";

/** Whether an announcement falls between the list day and the cycle expiry, inclusive. A date that
 *  does not reach that far is `unknown`, never `clear`; `none` is a fund, which never announces. */
export type EarningsInCycle = "in-cycle" | "clear" | "unknown" | "none";

export interface ScreenerLeg {
  role: "short" | "long";
  right: "P" | "C";
  expiry: string;
  strike: number;
  bid: number | null;
  ask: number | null;
  openInterest: number | null;
  volume: number | null;
  /** Against the leg bars (10% of mid or a nickel, 100 open, 10 traded); null when unquoted. */
  passes: boolean | null;
}

export interface ScreenerRow {
  symbol: string;
  /** Short puts and covered calls are bullish; a put spread is bullish, a call spread bearish. */
  direction: "bullish" | "bearish";
  right: "P" | "C";
  price: number | null;
  /** The list's own IV rank, 0-100. */
  ivRank: number | null;
  /** Our nightly options-tradable verdict (weeklies and $100M a day); null when not judged. */
  liquid: boolean | null;
  expiry: string;
  dte: number | null;
  weekly: boolean;
  /** One strike, or [sell, buy] for a spread. */
  strikes: number[];
  /** Per share: the option's mid, or a spread's credit. */
  mid: number | null;
  bid: number | null;
  ask: number | null;
  rawReturnPct: number | null;
  annualisedPct: number | null;
  /** Strike against the share price, %. */
  distancePct: number | null;
  /** A spread's width and its credit as a share of it, %. */
  width: number | null;
  creditPct: number | null;
  /** Our rule's strike(s) for this row (same shape as `strikes`); null when the session's broker
   *  chain was not recorded or did not cover the row. */
  ours: Array<number | null> | null;
  earningsDate: string | null;
  earningsSource: "broker" | "list" | null;
  /** The broker marks a date it has not confirmed. */
  earningsEstimated: boolean | null;
  /** The standard monthly the user trades: 45-60 days out, else 30-60. */
  cycleExpiry: string | null;
  earnings: EarningsInCycle;
  /** When the liquidity readings were taken (regular hours, the next session); null when none. */
  readingAt: string | null;
  listedLegs: ScreenerLeg[];
  cycleLegs: ScreenerLeg[] | null;
  /** `listed` when every listed leg passes the bars, else `cycle` when a cycle monthly is listed,
   *  else `none`; null with no reading. */
  recommendation: "listed" | "cycle" | "none" | null;
  /** Whether the cycle monthly's own legs pass the bars too; null with no reading or no monthly. */
  cyclePasses: boolean | null;
}

export interface ScreenerPayload {
  list: ScreenerList;
  /** The list day shown; null when nothing has been saved. */
  day: string | null;
  /** Every saved list day, newest first. */
  days: string[];
  /** When the list was built and priced, as the list states it. */
  createdAt: string | null;
  pricesAt: string | null;
  /** Whether our rule scored this day (its broker chain was recorded). */
  scored: boolean;
  rows: ScreenerRow[];
}

export interface ScreenerRate {
  hit: number;
  n: number;
}

export interface ScreenerFidelitySession {
  day: string;
  shortPuts: { monthly: ScreenerRate; weekly: ScreenerRate; expiry: ScreenerRate; returns: ScreenerRate };
  coveredCalls: { monthly: ScreenerRate; weekly: ScreenerRate; expiry: ScreenerRate; returns: ScreenerRate };
  creditSpreads: { expiry: ScreenerRate; short: ScreenerRate; long: ScreenerRate; floor: ScreenerRate };
}

export interface ScreenerFidelityPayload {
  sessions: ScreenerFidelitySession[];
  /** Every scored session summed. */
  totals: Omit<ScreenerFidelitySession, "day"> | null;
}
