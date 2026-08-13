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
 * The experiment guide: what each of a module's arms/profiles is, what distinguishes it, and how it
 * got here. One shape for flies' arms and MEIC's risk profiles, because the question is the same and
 * a second copy of the answer is a second copy that can drift.
 *
 * Every field is derived from what the module actually runs off — its config's own notes, the
 * settings themselves, and the ledger — rather than being written out a second time in the UI. A
 * description that lives only in the console goes stale the first time someone retunes an arm and
 * not the page, and a stale description is worse than none because it gets trusted.
 */
export interface GuideNote {
  /** The config key the text came from (`_note`, `_history_note`, …), minus the leading underscore. */
  key: string;
  text: string;
}

export interface GuideOverride {
  key: string;
  value: unknown;
  /** The base value this replaces, or null when the key has no entry in the module's defaults. */
  fallback: unknown;
  inDefaults: boolean;
  /** Stated but equal to the base value — it changes nothing. */
  matchesDefault: boolean;
  /**
   * Most siblings state this key at this same value. It may differ from the base while separating
   * this one from almost nothing — eleven of flies' twelve arms share one `entry_windows` — so it is
   * a house convention rather than a distinguisher, which is the question this view answers. The one
   * that departs from the convention still gets it as a difference, which is right: that departure
   * is exactly what the arm is testing.
   */
  sharedByMostArms: boolean;
}

/** A fact the config cannot show on its own, derived the way the engine derives it. */
export interface GuideDerived {
  label: string;
  value: string;
  detail: string | null;
}

export interface ExperimentGuideEntry {
  name: string;
  enabled: boolean;
  /** Disabled but present in the ledger — a finished experiment, not a typo. */
  retired: boolean;
  /** In the ledger but gone from the config entirely — explains rows the History tab still shows. */
  removed: boolean;
  notes: GuideNote[];
  overrides: GuideOverride[];
  derived: GuideDerived[];
  firstSession: string | null;
  lastSession: string | null;
  positions: number;
}

export interface ExperimentGuide {
  module: "flies" | "meic";
  mode: TradingMode;
  /** What the module calls these — "arm" or "risk profile". */
  unit: string;
  /** Notes attached to the block as a whole (e.g. a retired width sweep). */
  groupNotes: GuideNote[];
  /** Dates either side of which sessions must not be pooled, from the module's own record. */
  breaks: Array<{ date: string; scope: string; kind: string; reason: string }>;
  entries: ExperimentGuideEntry[];
  /** True when the config could not be read — the page says so rather than showing none. */
  configMissing: boolean;
}
