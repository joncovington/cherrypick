/**
 * The fair-value file (`packages/technicals` `valuation`, 2026-10-27): one DCF estimate a liquid
 * optionable name a session, from `cherrypick.core.valuation` on Dolt's annual statements. Every
 * figure is the builder's; the reader renames, badges pmcc's symbols from pmcc's own config, and
 * decides nothing. A model estimate of value with NO measured edge; never a ranking.
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
