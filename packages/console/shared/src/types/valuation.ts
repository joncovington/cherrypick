/**
 * The fair-value file (`packages/technicals` `valuation`, 2026-10-27): one DCF estimate a liquid
 * optionable name a session, from `cherrypick.core.valuation` on Dolt's annual statements. Every
 * figure is the builder's; the reader renames, badges pmcc's symbols from pmcc's own config, and
 * decides nothing. A model estimate of value tested 2018-26 with NO edge; never a ranking.
 */

/** `valued` carries the figures; the others carry a `reason` instead. */
export type ValuationState = "valued" | "not-applicable" | "no-statements" | "refused";

/** The six fields every income-list row carries from the file (income-lists version 3). `fairState`
 *  adds `unbuilt` (no file for the session) and `unlisted` (the file has no row for the name); null on
 *  a list built before the fields existed. */
export interface FairValueFields {
  fairValue: number | null;
  fairLow: number | null;
  fairHigh: number | null;
  /** Price ÷ fair value − 1: positive when the price sits above the model's value. */
  fairGap: number | null;
  fairState: ValuationState | "unbuilt" | "unlisted" | null;
  /** The builder's flags: `terminal_heavy`, `negative_base_fcf`, `beta_bounded`, `beta_disagrees`, `shares_stale`. */
  fairFlags: string[];
}

export interface ValuationRow {
  symbol: string;
  type: string | null;
  sector: string | null;
  state: ValuationState;
  reason: string | null;
  spot: number | null;
  spotDate: string | null;
  fiscalYearEnd: string | null;
  fairValue: number | null;
  low: number | null;
  high: number | null;
  /** Price ÷ fair value − 1. */
  gap: number | null;
  wacc: number | null;
  /** The bounded beta the WACC used, and the raw regression slope. */
  beta: number | null;
  betaRaw: number | null;
  /** The broker's beta from the IV-rank file, a cross-check only. */
  betaBroker: number | null;
  marketCap: number | null;
  marketCapBroker: number | null;
  growthY1: number | null;
  ebitMargin: number | null;
  /** The discounted terminal value's share of EV: above 0.75 the answer is the perpetuity. */
  terminalShare: number | null;
  impliedEvEbitda: number | null;
  /** The reverse DCF (core.valuation.implied): the long-run operating margin, and separately the
   *  year-one revenue growth (faded to the terminal rate by year five), at which the base case
   *  equals the price, every other driver held. Null margin: none under 100% of revenue reaches it. */
  impliedMargin: number | null;
  impliedGrowth: number | null;
  /** "above" or "below" when the growth the price needs sits outside the search band; the
   *  growth is then that edge. */
  impliedGrowthEdge: "above" | "below" | null;
  /** The company's best operating margin and best year-over-year revenue growth of the last
   *  five fiscal years. */
  bestMargin: number | null;
  bestGrowth: number | null;
  /** The builder's read: "below value" (the price asks nothing new), "within history" (either
   *  lever alone gets there without beating the company's best), "beyond history". */
  reach: "below value" | "within history" | "beyond history" | null;
  consensusUsed: boolean;
  flags: string[];
  /** One of pmcc's configured symbols (its own config, read by the console). */
  pmcc: boolean;
}

export interface ValuationPayload {
  /** The session shown; null when no file has been built. */
  day: string | null;
  days: string[];
  builtAt: string | null;
  universe: string | null;
  names: number;
  /** Row counts by state (a non-valued state keyed `state: reason`), as the builder counted them. */
  states: Record<string, number>;
  rule: {
    forecastYears: number | null;
    terminalGrowth: number | null;
    riskFree: number | null;
    equityPremium: number | null;
    beta: string | null;
    edge: string | null;
  };
  rows: ValuationRow[];
}
