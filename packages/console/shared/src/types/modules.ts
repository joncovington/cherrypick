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

// --------------------------------------------------------------------------- suite review
// Shapes mirror `packages/review`'s fact set (data/review/eod-<day>.json). The console renders that
// artifact and derives nothing from the ledgers, which is what keeps this page, the markdown render
// and the narrative from holding different opinions about a session.

export interface ReviewArm {
  arm: string;
  closed: number;
  net: number;
  wins: number;
  capitalAtRisk: number | null;
  onMaxRisk: number | null;
  /** The centring rule when every entry used one — the tell for an arm that collapsed into another. */
  centredBy: string | null;
}

export interface ReviewModule {
  module: string;
  ok: boolean;
  reason: string | null;
  loopTicked: boolean | null;
  iterations: number | null;
  errors: number | null;
  closed: number;
  net: number;
  gross: number;
  cost: number;
  wins: number;
  capitalAtRisk: number | null;
  onMaxRisk: number | null;
  n: number | null;
  effectiveN: number | null;
  /** null = the module tracks no breaks at all, which is weaker than an empty list, not stronger. */
  breaks: string[] | null;
  suspectedBreak: { ratio: number; trades: number; trailingMedian: number } | null;
  expectedBasis: string | null;
  expected: number | null;
  observed: number | null;
  carriedPositions: number;
  carriedCapital: number | null;
  arms: ReviewArm[];
}

export interface ReviewSession {
  session: string;
  status: string;
  factVersion: number | null;
  generatedAt: string | null;
  modules: ReviewModule[];
  note: string | null;
}

export interface ReviewPayload {
  sessions: string[];
  current: ReviewSession | null;
  allTime: {
    sessions: number;
    from: string | null;
    to: string | null;
    netByModule: Record<string, number>;
    closedByModule: Record<string, number>;
  };
}

/**
 * The flies arm guide: what each experiment arm is, what distinguishes it, and how it got here.
 *
 * Every field is derived from what the module actually runs off — the deployed config's own notes,
 * the diff between an arm's overrides and the shared defaults, and the ledger — rather than being
 * written out a second time in the UI. An arm's description that lives only in the console is one
 * that goes stale the first time someone retunes the arm and not the page.
 */
export interface FliesArmNote {
  /** The config key the text came from (`_note`, `_history_note`, …), minus the leading underscore. */
  key: string;
  text: string;
}

export interface FliesArmOverride {
  key: string;
  value: unknown;
  /** The shared default this replaces, or null when the key has no entry in `defaults`. */
  fallback: unknown;
  inDefaults: boolean;
  /** The arm states this but it equals the shared default — it changes nothing. */
  matchesDefault: boolean;
  /**
   * Most arms state this key at this same value. It may differ from `defaults` while separating
   * this arm from almost nothing — eleven of the twelve arms share one `entry_windows` — so it is a
   * house convention rather than a distinguisher, which is the question this view exists to answer.
   * The one arm that departs from the convention still gets it as a difference, which is right:
   * that departure is exactly what `time_window` is testing.
   */
  sharedByMostArms: boolean;
}

export interface FliesArmGuideEntry {
  arm: string;
  enabled: boolean;
  /**
   * The centring the engine will actually use, derived exactly as `engine.select_center` derives
   * it: `center_rule` when the arm sets one, otherwise the arm's OWN NAME. Without this the headline
   * comparison is invisible — the `gex` arm carries no `center_rule` key, so a pure config diff
   * reports nothing separating it from `control` when the centring rule is the whole experiment.
   */
  centring: "gex" | "atm";
  /** True when the centring came from the arm's name rather than an explicit `center_rule`. */
  centringFromName: boolean;
  /** Disabled but present in the ledger — a finished experiment, not a typo. */
  retired: boolean;
  notes: FliesArmNote[];
  /** What this arm changes relative to `defaults`. This IS the arm's hypothesis, mechanically. */
  overrides: FliesArmOverride[];
  firstSession: string | null;
  lastSession: string | null;
  positions: number;
}

export interface FliesArmGuide {
  mode: TradingMode;
  /** Notes attached to the arms block as a whole (e.g. the retired width sweep). */
  groupNotes: FliesArmNote[];
  /** Dates either side of which sessions must not be pooled, from the module's own record. */
  breaks: Array<{ date: string; scope: string; kind: string; reason: string }>;
  arms: FliesArmGuideEntry[];
  /** True when the deployed config could not be read — the page says so rather than showing none. */
  configMissing: boolean;
}
