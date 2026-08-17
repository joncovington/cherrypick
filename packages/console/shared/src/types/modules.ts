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

// ---- PMCC-99 ----
//
// Paper-only: there is no live DB and no live loop, so nothing here carries a `mode`. The module's
// `live.enabled` is a documented placeholder (see packages/pmcc/CLAUDE.md), and offering a mode
// toggle over a book that cannot exist would be a lie the type system can prevent.
//
// Nulls are load-bearing throughout. `analytics.py` states the rule the whole module keeps: `None`
// never means zero, because "not recorded" and "was zero" are different facts. Every nullable field
// below is one the reader must leave null rather than defaulting.

/** One open position, mirroring `analytics.worksheet()` plus its latest usable short mark. */
export interface PmccOpenPosition {
  positionId: string;
  symbol: string;
  book: string;
  /** `open`, or `short_settled` — the short expired ITM and its shares await next-session disposal. */
  status: string;
  longStrike: number | null;
  longExpiration: string | null;
  shortStrike: number | null;
  shortExpiration: string | null;
  entrySpot: number | null;
  netDebit: number | null;
  entryNetTv: number | null;
  entryWeeklyYieldPct: number | null;
  downsideProtectionPct: number | null;
  breakeven: number | null;
  rollCount: number | null;
  /** Latest usable short-leg mark. Null means no usable mark yet — never render it as 0. */
  currentShortTv: number | null;
  currentSpot: number | null;
  lastMarkAt: number | null;
  /** Per-position early-assignment exposure, from `analytics.exposure()`. */
  exposedTicks: number;
  markedTicks: number;
}

/** Per-book, per-symbol results over CLOSED positions — `analytics.headline()`. */
export interface PmccBookCell {
  book: string;
  symbol: string;
  positions: number;
  grossPnl: number | null;
  fees: number | null;
  /** `gross_pnl - fees`, the suite's one net convention. Null if either side is unrecorded. */
  netPnl: number | null;
  winRate: number | null;
  rolls: number | null;
}

/** A symbol's declared ex-dividend calendar. A lapsed table refuses entries loudly, by design. */
export interface PmccDividendRow {
  symbol: string;
  declaredThrough: string | null;
  exDates: string[];
  /** Within 14 days of lapsing (or already lapsed) — a ~9DTE short can span past it before then. */
  refreshDue: boolean;
}

/** Keltner cold-start progress per symbol — `analytics.keltner_readiness()`. */
export interface PmccKeltnerReadiness {
  symbol: string;
  bars: number;
  required: number;
}

/** One symbol's Keltner channel, recomputed exactly as `keltner.py` draws it. */
export interface PmccKeltnerSeries {
  symbol: string;
  /** Oldest first. `mid`/`upper`/`lower` are null until enough history exists to seed the channel. */
  points: Array<{ date: string; close: number | null; mid: number | null; upper: number | null; lower: number | null }>;
  /** The gate's current verdict, from the day's own attempts — one failing condition, or null when it passed. */
  gate: { reason: string | null; occurrences: number } | null;
}

/** The honesty surface: everything that bounds how far the paper net can be trusted. */
export interface PmccIntegrity {
  exposure: { positionsWithExposure: number; exposedTicks: number; markedTicks: number };
  dividends: PmccDividendRow[];
  keltner: PmccKeltnerReadiness[];
  markCoverage: {
    session: string | null;
    marks: number;
    refused: number;
    refusalShare: number | null;
    refusals: Array<{ reason: string; n: number }>;
  };
  /** Columns the ledger has that this console build does not know — the writer is newer than this page. */
  schemaDrift: string[];
  measurementBreaks: Array<{ date: string; key: string; note: string | null }>;
}

export interface PmccPayload {
  /** The resolved session every card on the page names. Null when the module has never run. */
  session: string | null;
  /** False when the store is absent — "has not run here", which is not an error. */
  dbPresent: boolean;
  openPositions: PmccOpenPosition[];
  openCount: number;
  books: PmccBookCell[];
  integrity: PmccIntegrity;
  keltner: PmccKeltnerSeries[];
  today: {
    attempts: Array<{ book: string; outcome: string; n: number; blockDetail: string | null; bestYield: number | null }>;
    events: Array<{ action: string; reason: string; executed: boolean; gate: string | null; n: number }>;
    lastIteration: { ranAt: number; phase: string; status: string; ageSeconds: number } | null;
  };
  /** The declared knobs the cards render against — thresholds, not preferences. */
  params: {
    tvCloseThreshold: number | null;
    assignmentExposureTv: number | null;
    targetWeeklyYieldMin: number | null;
    keltnerMinHistory: number | null;
    symbols: string[];
  };
}

/** One short leg in a cycle's chain. Rolls append, so a cycle can hold several. */
export interface PmccShortLeg {
  legRole: string;
  strike: number | null;
  expiration: string | null;
  /** traded | rolled | expired | assigned | cash_settled */
  closeKind: string | null;
  closeValue: number | null;
}

export interface PmccRoll {
  session: string | null;
  oldStrike: number | null;
  newStrike: number | null;
  oldExpiration: string | null;
  newExpiration: string | null;
  netRollCredit: number | null;
}

/** Shares delivered by a physically-settled ITM short, booked at the SETTLEMENT SPOT, not the strike. */
export interface PmccAssignment {
  legRole: string;
  direction: string;
  shares: number | null;
  basis: number | null;
  strike: number | null;
  status: string;
  disposedSession: string | null;
  disposalPrice: number | null;
  sharePnl: number | null;
}

/** One completed cycle: entry through exit, with the whole short chain it took to get there. */
export interface PmccCycleRow {
  positionId: string;
  symbol: string;
  book: string;
  entrySession: string;
  closedSession: string | null;
  status: string;
  exitReason: string | null;
  longStrike: number | null;
  longExpiration: string | null;
  entrySpot: number | null;
  settlementSpot: number | null;
  netDebit: number | null;
  entryNetTv: number | null;
  entryWeeklyYieldPct: number | null;
  rollCount: number | null;
  itmSettlements: number | null;
  grossPnl: number | null;
  fees: number | null;
  netPnl: number | null;
  shorts: PmccShortLeg[];
  rolls: PmccRoll[];
  assignments: PmccAssignment[];
}

export interface PmccMeta {
  books: string[];
  symbols: string[];
  sessions: string[];
}

