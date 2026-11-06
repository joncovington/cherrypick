/**
 * The time-spread scanner (docs/time-spread-scanner-plan.md): calendars and diagonals on names whose
 * front week is priced rich to the back month. `scripts/fetch_time_spreads.py` writes one file a
 * session (`time-spreads/<session>.json`) with every name's term-structure row and the shortlist's
 * structures as manage builds them; manage sizes them to the account (`book.time_spreads`). Every
 * figure is the producers'; the readers rename and decide nothing. NO TESTED EDGE: rows are ordered by
 * the front/back IV ratio, an untested hypothesis (item 54).
 */

/** One name's term-structure read and its prefilter verdict. */
export interface TimeSpreadTerm {
  symbol: string;
  ratio: number | null;
  ivRank: number | null;
  front: { expiration: string; dte: number; iv: number } | null;
  back: { expiration: string; dte: number; iv: number } | null;
  earningsDate: string | null;
  sector: string | null;
  passes: boolean;
  why: string[];
}

/** One calendar or diagonal, modelled at each leg's own IV. Per share unless named `pnl*`/`maxLoss`
 *  (whole-position dollars for one unit). The sizing fields are set only on the account's rows. */
export interface TimeSpreadStructure {
  underlying: string;
  kind: string;
  direction: "neutral" | "bullish" | "bearish";
  label: string;
  front: string;
  back: string;
  spot: number | null;
  ratio: number | null;
  ivRank: number | null;
  sector: string | null;
  debitMid: number | null;
  debitNatural: number | null;
  gap: number | null;
  debitShareOfGap: number | null;
  frontIv: number | null;
  backIv: number | null;
  netDelta: number | null;
  thetaDay: number | null;
  pnlFlat: number | null;
  pnlUp5: number | null;
  pnlDown10: number | null;
  losesUnchanged: boolean;
  breakeven: [number | null, number | null] | null;
  priceStop: [number | null, number | null] | null;
  takeProfit: number | null;
  valueStop: number | null;
  rollBy: string | null;
  maxLoss: number | null;
  units: number | null;
  binding: string | null;
  heldName: boolean;
  heldSector: boolean;
  bpAfterPct: number | null;
}

/** The Screeners tab's payload: one session's file. */
export interface TimeSpreadsPayload {
  day: string | null;
  days: string[];
  takenAt: string | null;
  complete: boolean;
  problem: string | null;
  order: string | null;
  universe: number | null;
  read: number | null;
  passing: number | null;
  shortlist: string[];
  /** Passing names first (by ratio), then the rest by name. */
  terms: TimeSpreadTerm[];
  structures: TimeSpreadStructure[];
  dropped: Record<string, number>;
  error: string | null;
}

/** The account's rows (`book.time_spreads`), on Live, portfolio. */
export interface ManageTimeSpreads {
  sourceSession: string | null;
  takenAt: string | null;
  order: string | null;
  edge: string | null;
  deltaFilter: { bookBwd: number | null; limit: number | null; bullishHeldBack: boolean } | null;
  bpCapPct: number | null;
  fits: TimeSpreadStructure[];
  fitsTotal: number;
  tooBig: TimeSpreadStructure[];
  tooBigTotal: number;
  dropped: Record<string, number>;
  error: string | null;
}
