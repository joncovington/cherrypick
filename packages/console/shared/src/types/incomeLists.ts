/**
 * Our own short-put and covered-call lists (`packages/technicals` `income-lists`, 2026-10-19): one row
 * a name, built from the afternoon income chains with the suite's 16-delta rule. Every figure is the
 * builder's; the reader renames and decides nothing.
 */

export type IncomeList = "short-puts" | "covered-calls";

/** The next announcement against the trade's own expiry; `none` for a fund, which reports none. */
export type IncomeEarnings = "in-trade" | "clear" | "unknown" | "none";

/** A covered call's ex-dividend date against its expiry. Only `after` is clear of the trade. */
export type IncomeExDividend = "in-trade" | "after" | "past" | "unknown";

export interface IncomeListRow {
  symbol: string;
  /** The security list's type (Stock, Exchange Traded Fund, ...), null where it has none. */
  type: string | null;
  spot: number;
  expiry: string;
  dte: number;
  /** Covered calls: "30-45" (the monthly 30-45 days out) or "cycle" (none was listed). */
  window: string | null;
  strike: number;
  /** Black-Scholes on the option's own IV, solved from its mid: negative for a put. */
  delta: number;
  iv: number;
  bid: number | null;
  ask: number | null;
  mid: number;
  /** Per share, the mid less a quarter of the width: what a limit at the mid stepped down fills at. */
  working: number | null;
  /** Whole contract: working × 100. */
  premium: number | null;
  /** Working premium over the cash tied up (the strike for a put, the share price for a call), a fraction. */
  returnFraction: number | null;
  /** returnFraction × 365 ÷ days, simple. */
  annualFraction: number | null;
  /** Strike against the share price, a fraction (negative below it). */
  distanceFraction: number;
  openInterest: number | null;
  volume: number | null;
  /** The reading's own verdict against the leg bars; null when unquoted. */
  passes: boolean | null;
  quiet: boolean | null;
  earnings: IncomeEarnings;
  earningsDate: string | null;
  earningsEstimated: boolean | null;
  /** Covered calls only: an ex-dividend date between today and expiry (early assignment, flagged).
   *  False never meant clear: it was also a past date or none at all. */
  exDividendInTrade: boolean | null;
  /** Covered calls only: the broker's ex-dividend date against the trade -- between today and expiry,
   *  after it, already past (the next not posted) or no date. Null for a put, and on a list built
   *  before the state was recorded (2026-10-21), which carries only `exDividendInTrade`. */
  exDividend: IncomeExDividend | null;
  exDividendDate: string | null;
  /** `leveraged`, `inverse` or `volatility` for a declared daily-reset fund; null otherwise, and on a
   *  list built before the badge existed. */
  leveraged: string | null;
  /** Whether the trade fits the account, as manage judged it for the session (`candidates.screen_fit`):
   *  a short put within the buying power available, a covered call on an uncovered 100-share lot.
   *  null when unjudged -- no evaluation for the session, or a strike manage did not price. */
  fit: boolean | null;
}

export interface IncomeListPayload {
  list: IncomeList;
  /** The session shown; null when no list has been built. */
  day: string | null;
  /** Every built session, newest first. */
  days: string[];
  builtAt: string | null;
  chainTakenAt: string | null;
  chainComplete: boolean | null;
  chainProblem: string | null;
  /** Names the chain reading held; rows are the ones a strike could be picked for. */
  names: number;
  /** Why a name has no row, by reason. */
  dropped: Record<string, number>;
  /** The deltas the builder picked at, as it recorded them. */
  targetDelta: number | null;
  rows: IncomeListRow[];
  /** What `fit` was judged against: manage's evaluation of the same session; null with none. */
  fitBasis: { session: string; availableBp: number | null; basis: string | null } | null;
}
