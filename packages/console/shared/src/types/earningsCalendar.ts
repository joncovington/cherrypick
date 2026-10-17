/**
 * The earnings calendar (2026-10-17; docs/earnings-screener-plan.md): every announcement in the next
 * 30 days, as `python -m cherrypick.technicals earnings-calendar` writes it each evening
 * (`technicals/earnings-calendar/<day>.json`). The console reads the file and re-derives nothing.
 */

/** The direction read's codes, as the security list spells them; `edgeLabel` carries the words. */
export type EarningsEdge = "sb" | "bn" | "bp" | "bi" | "mx" | "br" | "bd" | "brn" | "sbe";

/** The navigator's spread for a strong read: target strikes, not yet snapped to listed ones. */
export interface EarningsSuggestion {
  kind: "debit" | "credit";
  right: "P" | "C";
  long: number;
  short: number;
  /** The trade's expiration (the cycle monthly by default); null when none is listed in range. */
  expiry: string | null;
}

export interface EarningsCalendarRow {
  symbol: string;
  company: string | null;
  date: string;
  /** Before or after the open; null when no source says, never assumed. */
  timing: "bmo" | "amc" | null;
  /** Which source gave the time: the broker (with its own date) or Dolt (its date within a day). */
  timingSource: "broker" | "dolt" | null;
  dateSource: "broker" | "dolt";
  /** The broker confirmed the date (true), marks it estimated (false), or Dolt's date is shown (null). */
  dateConfirmed: boolean | null;
  /** Days between the broker's date and Dolt's, where both have one. */
  dateGapDays: number | null;
  /** The last close before the news; null when the time of day is unknown. */
  entrySession: string | null;
  reactionDay: string;
  type: string | null;
  /** The vendor's region filter: depository receipts are not US. */
  region: "US" | "intl" | null;
  edge: EarningsEdge | null;
  edgeLabel: string | null;
  /** The 1-10 strength (the security list's technical rank). */
  strength: number | null;
  ivRank: number | null;
  /** 1 very liquid, 2 somewhat, 3 not. */
  liquidityRank: number | null;
  optionable: boolean | null;
  sector: string | null;
  price: number | null;
  eventExpiry: string | null;
  eventIv: number | null;
  /** One standard deviation to the event expiry, in dollars and as a share of the price (%). */
  sdMove: number | null;
  sdMovePct: number | null;
  /** The suite's expected move (0.85 x the straddle, ~0.68 SD), in dollars. */
  emMove: number | null;
  suggestion: EarningsSuggestion | null;
}

export interface EarningsCalendarPayload {
  /** The session shown; null when no calendar has been written. */
  day: string | null;
  /** Every saved session, newest first. */
  days: string[];
  /** What the calendar was built from: the security list's, the IVs', the broker dates' and the close's session. */
  inputs: { securityList: string | null; expiryIv: string | null; brokerDates: string | null; closeDate: string | null };
  ivRankCut: number | null;
  rows: EarningsCalendarRow[];
}
