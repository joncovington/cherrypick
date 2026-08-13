import type { TradingMode } from "./status.js";

/**
 * One page of a larger result. `total` counts every row matching the scope and
 * filters, not the ones returned — a table that reports its page size as its
 * count reads like an answer while hiding the rest.
 */
export interface Paged<T> {
  rows: T[];
  total: number;
  offset: number;
  limit: number;
}

// ---- MEIC ----

export interface MeicTradeRow {
  mode: TradingMode;
  id: number;
  tradeDate: string;
  entryTime: string | null;
  symbol: string;
  putStrike: number | null;
  callStrike: number | null;
  wingWidth: number | null;
  netCredit: number | null;
  quantity: number | null;
  status: string;
  pnl: number | null;
  fees: number | null;
  exitReason: string | null;
  ivRankAtEntry: number | null;
}

export interface MeicSummaryRow {
  mode: TradingMode;
  summaryDate: string;
  symbol: string | null;
  totalEntries: number | null;
  entriesFilled: number | null;
  entriesStopped: number | null;
  netPnl: number | null;
  winRatePct: number | null;
}

export interface MeicPayload {
  mode: TradingMode;
  /** The trade log, newest first — one page of it. */
  trades: Paged<MeicTradeRow>;
  summaries: MeicSummaryRow[];
}

// ---- Flies ----

export interface FliesBookRow {
  mode: TradingMode;
  bookId: string;
  tradeDate: string;
  arm: string | null;
  symbol: string;
  creditCollected: number | null;
  debitsPaid: number | null;
  fees: number | null;
  netCash: number | null;
  floorHolds: boolean | null;
  bandLow: number | null;
  bandHigh: number | null;
  pnl: number | null;
  status: string;
}

export interface FliesPositionRow {
  mode: TradingMode;
  positionId: string;
  tradeDate: string;
  symbol: string;
  arm: string | null;
  entryMode: string | null;
  kind: string | null;
  side: string | null;
  center: number | null;
  wingWidth: number | null;
  quantity: number | null;
  net: number | null;
  floorDollars: number | null;
  riskFree: boolean;
  status: string;
  pnl: number | null;
  entryTime: string | null;
}

export interface FliesPayload {
  mode: TradingMode;
  /** Two tables, paged independently — a page turn in one leaves the other alone. */
  books: Paged<FliesBookRow>;
  positions: Paged<FliesPositionRow>;
}

// ---- Earnings ----

export interface EarningsTradeRow {
  mode: TradingMode;
  orderId: string;
  symbol: string;
  strategy: string;
  expiration: string | null;
  entryCredit: number | null;
  pnl: number | null;
  quantity: number | null;
  openedAt: string | null;
  closedAt: string | null;
  profile: string | null;
}

export interface EntryReviewRow {
  mode: TradingMode;
  scanDate: string;
  symbol: string;
  timing: string | null;
  winrate: number | null;
  ivRvRatio: number | null;
  expectedMove: number | null;
  selected: boolean;
  reason: string | null;
}

/** Earnings browses both books at once, like scout does. */
export interface EarningsPayload {
  trades: Paged<EarningsTradeRow>;
  reviews: Paged<EntryReviewRow>;
}

// ---- GEX ----

export interface GexRegimeRow {
  symbol: string;
  tradeDate: string;
  ts: string;
  spot: number | null;
  netGex: number | null;
  netGexVol: number | null;
  zeroGamma: number | null;
  callWall: number | null;
  putWall: number | null;
}

export interface GexPayload {
  /** Most recent regime row per symbol. */
  latest: GexRegimeRow[];
  /** Recent history (latest day) for the table, newest first. */
  recent: GexRegimeRow[];
}
