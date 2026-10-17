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

/** One leg of the suggested spread on a listed strike, with its regular-hours quote. */
export interface EarningsTradeLeg {
  role: "long" | "short";
  strike: number;
  bid: number | null;
  ask: number | null;
  openInterest: number | null;
  volume: number | null;
  /** Against the leg bars (10% of mid or a nickel, 100 open, 10 traded); null when unquoted. */
  passes: boolean | null;
}

/** The suggested spread as `fetch_screener_greeks.py measure` read it: snapped, quoted, priced. */
export interface EarningsTrade {
  readingAt: string | null;
  kind: "debit" | "credit";
  right: "P" | "C";
  expiry: string;
  width: number;
  legs: EarningsTradeLeg[];
  /** Per share: at mid, and at the natural fill (pay the ask, sell at the bid). */
  mid: number | null;
  natural: number | null;
  shareOfWidth: number | null;
  /** One spread, dollars. */
  maxLoss: number | null;
  /** Every leg passes the bars; null while a leg is unquoted. */
  passes: boolean | null;
}

export interface EarningsCalendarRow {
  symbol: string;
  company: string | null;
  date: string;
  /** Before or after the open; null when no source says, never assumed. */
  timing: "bmo" | "amc" | null;
  /** Which source gave the time: the broker (with its own date), Nasdaq's calendar or Dolt (each
   *  for a date within a day). */
  timingSource: "broker" | "nasdaq" | "dolt" | null;
  /** Where no source gives the time: the name's last timed report's, an ESTIMATE (right 97% of the
   *  time historically). It sets neither the entry session nor the reaction day. */
  usualTiming: "bmo" | "amc" | null;
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
  /** OUR direction read (Bearing, from our bars) -- the vendor's is not shown or used (2026-10-17). */
  edge: EarningsEdge | null;
  edgeLabel: string | null;
  /** OUR 1-10 strength, from the same bars. */
  strength: number | null;
  /** The close our read and strength are as of (the store's last bar). */
  readSession: string | null;
  /** The BROKER's IV rank, 0-100 (it can read above 100): shown, and what picks the spread's side. */
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
  /** The expected move the tab shows: the broker's earnings rule, 0.85 x the ATM straddle (~0.68
   *  SD), in dollars and as a share of the price (%). */
  emMove: number | null;
  emMovePct: number | null;
  suggestion: EarningsSuggestion | null;
  /** The suggestion on listed strikes and its regular-hours reading; null until one is taken. */
  trade: EarningsTrade | null;
  /** The suggestion on listed strikes from the evening's chain listing, no quotes yet: [long,
   *  short]. Null where no listing was read; a regular-hours reading (`trade`) supersedes it. */
  listed: { expiry: string; strikes: number[] } | null;
}

/** One spread's money on one mark, in the suite's layout: signed whole-position cash, costs apart. */
export interface EarningsRecordTrade {
  entry: number;
  exit: number;
  gross: number;
  fees: number;
  slippage: number;
  net: number;
  risk: number;
}

export interface EarningsRecordSpread {
  /** "entered", or why not: "not read on the entry session" / "not listed on the entry session". */
  state: string;
  kind: "debit" | "credit" | null;
  right: "P" | "C" | null;
  expiry: string | null;
  strikes: number[] | null;
  entryMid: number | null;
  liquidAtEntry: boolean | null;
  react: EarningsRecordTrade | null;
  day2: EarningsRecordTrade | null;
}

/** One past announcement: what the calendar showed the session before, and what happened. */
export interface EarningsRecordRow {
  symbol: string;
  company: string | null;
  date: string;
  calendarDay: string;
  timing: "bmo" | "amc" | null;
  reactionDay: string;
  edge: EarningsEdge | null;
  edgeLabel: string | null;
  liquidityRank: number | null;
  region: "US" | "intl" | null;
  movePct: number | null;
  emMovePct: number | null;
  moveOverEm: number | null;
  insideEm: boolean | null;
  directionRight: boolean | null;
  spread: EarningsRecordSpread | null;
}

export interface EarningsRecordTotals {
  n: number;
  net: number;
  won: number | null;
  returnOnRisk: number | null;
}

export interface EarningsRecordPayload {
  /** Null until `technicals earnings-record` has written one. */
  asOf: string | null;
  summary: {
    announcements: number;
    measured: number;
    insideEm: number | null;
    medianMoveOverEm: number | null;
    strongReads: number;
    directionRight: number | null;
    react: EarningsRecordTotals;
    day2: EarningsRecordTotals;
  } | null;
  rows: EarningsRecordRow[];
}

export interface EarningsCalendarPayload {
  /** The session shown; null when no calendar has been written. */
  day: string | null;
  /** Every saved session, newest first. */
  days: string[];
  /** What the calendar was built from: the security list's, the IVs', the broker dates' and the close's session. */
  inputs: {
    securityList: string | null;
    expiryIv: string | null;
    brokerDates: string | null;
    nasdaqTimes: string | null;
    closeDate: string | null;
  };
  ivRankCut: number | null;
  rows: EarningsCalendarRow[];
}
