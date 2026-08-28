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

/**
 * One journaled measurement break. Results either side of the date must not be pooled.
 *
 * `scope` is present on the modules whose ledger records one (meic and flies scope a break to an
 * arm, or `*` for the whole book); the LedgerStore shape the newer modules use has no scope, and
 * those simply omit it.
 */
export interface MeasurementBreak {
  date: string;
  key: string;
  note: string | null;
  scope?: string | null;
}

/** What bounds how far a module page's numbers can be trusted. */
export interface ModuleIntegrity {
  measurementBreaks: MeasurementBreak[];
  /** Columns the ledger has that this build does not know — a stale-checkout signal. */
  schemaDrift: string[];
}

export interface MeicPayload {
  mode: TradingMode;
  /** The trade log, newest first — one page of it. */
  trades: Paged<MeicTradeRow>;
  summaries: MeicSummaryRow[];
  integrity: ModuleIntegrity;
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
  integrity: ModuleIntegrity;
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
  /**
   * The latest day's regime samples, newest first, PAGED.
   *
   * This was a bare array with a hidden `LIMIT 60` in the query. A session records 240-288 rows,
   * so the table showed roughly a fifth of the day and said nothing about the rest -- a silent cap,
   * which reads as "this is the whole day" when it is not.
   */
  recent: Paged<GexRegimeRow>;
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
  /**
   * Era totals: fact sets summed from the suite's declared era (`data_epoch`) onward. Was
   * `allTime` until the 2026-08-21 advisor-era cutover — a total pooled across that boundary reads
   * as one experiment when it is really two incomparable ones. Null `eraFrom` = no declared era =
   * everything, labeled accordingly.
   */
  era: {
    eraFrom: string | null;
    eraNote: string | null;
    sessions: number;
    from: string | null;
    to: string | null;
    netByModule: Record<string, number>;
    closedByModule: Record<string, number>;
    /** Per-module per-session nets in session order — the sparkline series, collected in the same
     *  pass as the totals so a line and the tile above it cannot disagree. */
    trendByModule: Record<string, Array<{ session: string; net: number }>>;
    /** Suite net per session (every readable module summed) — the Overview calendar strip's
     *  series. A session with no readable module is ABSENT, never a zero day. */
    suiteDaily: Array<{ session: string; net: number; closed: number }>;
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
//
// 2026-08-23 redesign (measurement break, see packages/pmcc/CLAUDE.md): TQQQ (American, physical
// settlement), single-book (`control` + its synthetic `advised:control` twin), with XSP (Mini-SPX,
// European, cash-settled) added the same day as a second symbol run as a separate population under
// the identical rule set. The old `keltner`/`roll` books,
// the Keltner-channel gate and the roll chain are RETIRED going forward — there is no more keltner
// readiness/series to mirror, and `pmcc_management_events` will never again record `roll_short`.
// `rollCount`/`PmccRoll` stay in this file only because the columns/rows are additive history: a
// pre-redesign row can carry a nonzero `roll_count` or a recorded roll, and the history tab still
// needs to render that era honestly. Every NEW row will report `rollCount: 0` and an empty
// `rolls` array — read a nonzero value here as pre-2026-08-23 history, not a live behavior.

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
  /** Always 0 on a row opened after the 2026-08-23 redesign — there is no more roll book. A
   *  pre-redesign row may carry a nonzero historical value. */
  rollCount: number | null;
  /** Latest usable short-leg mark. Null means no usable mark yet — never render it as 0. */
  currentShortTv: number | null;
  currentSpot: number | null;
  lastMarkAt: number | null;
  /** Per-position early-assignment exposure, from `analytics.exposure()`. */
  exposedTicks: number;
  markedTicks: number;
  /**
   * The widest leg spread AT ENTRY, as a fraction of that leg's mid, and in dollars per share.
   *
   * Sits beside the yield because on deep-ITM legs the two are the same size: a structure quoting a
   * $3.55 spread to capture $0.36 of time value is not a thin edge, it is a negative one, and the
   * yield alone cannot say so.
   */
  entryMaxSpreadPct: number | null;
  entryMaxSpreadAbs: number | null;
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

/** The honesty surface: everything that bounds how far the paper net can be trusted. */
export interface PmccIntegrity {
  exposure: { positionsWithExposure: number; exposedTicks: number; markedTicks: number };
  dividends: PmccDividendRow[];
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
  today: {
    attempts: Array<{
      symbol: string;
      book: string;
      outcome: string;
      n: number;
      blockDetail: string | null;
      bestYield: number | null;
    }>;
    /** `symbol` is null for an event whose position row could not be joined (e.g. the position was
     *  since purged) -- rendered as "every symbol" rather than dropped. */
    events: Array<{ symbol: string | null; action: string; reason: string; executed: boolean; gate: string | null; n: number }>;
    lastIteration: { ranAt: number; phase: string; status: string; ageSeconds: number } | null;
  };
  /** The declared knobs the cards render against — thresholds, not preferences. */
  params: {
    tvCloseThreshold: number | null;
    /**
     * Whether the pre-redesign early-tv-exhaustion exit is live. Config-level `defaults` reads
     * false/off by default; the only place it can be true in practice is a frozen
     * `advised:control` row's `advice_params` overlay, since control itself always holds to
     * `short_expiration`. Rendered so the page can say WHICH exit rule a book is running under.
     */
    tvManagedExit: boolean | null;
    assignmentExposureTv: number | null;
    longDeltaMin: number | null;
    longDeltaMax: number | null;
    symbols: string[];
    /** Per-symbol settlement style ("physical" | "cash"), read straight through from the module's
     *  own `settlement_style` config map. A symbol absent here means config doesn't declare one for
     *  it either. Cash-settled symbols (XSP) are European-exercise: no early-assignment exposure to
     *  measure and no ex-dividend refusal check — see `assignment_exposed`/`_entry_guards` in the
     *  module. Physical symbols (TQQQ) carry both. */
    settlementStyle: Record<string, string>;
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

/** Historical only since the 2026-08-23 redesign retired the roll book — a cycle opened after that
 *  date will never have one of these. Kept so pre-redesign history renders honestly. */
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
  /**
   * The fee stack, split.
   *
   * `fees` is the total and stays the one number net subtracts, but a total hides which half did the
   * damage. On this module's first session 98% of it was slippage — the commissions were $2.48
   * against $123.12 of crossing deep-ITM spreads — and a single figure cannot show that.
   */
  entryCost: number | null;
  exitCost: number | null;
  entrySlippage: number | null;
  exitSlippage: number | null;
  /** Widest leg spread at entry — see PmccOpenPosition for why it rides beside the yield. */
  entryMaxSpreadPct: number | null;
  entryMaxSpreadAbs: number | null;
  shorts: PmccShortLeg[];
  rolls: PmccRoll[];
  assignments: PmccAssignment[];
}

export interface PmccMeta {
  books: string[];
  symbols: string[];
  sessions: string[];
}

// --------------------------------------------------------------------------- calendars (dc_week)
//
// The weekly double-calendar module is a forward EXIT-PARAMETER EXPERIMENT, so its read model is
// shaped around two questions rather than a P&L: did this week enter, and what does the derived
// policy table say — beside the validation that says whether to believe it.

/** The week's computed anchors. Comes from `clock.week_plan`, never re-derived here. */
export interface CalendarsWeekPlan {
  weekOf: string;
  entrySession: string;
  frontExpiration: string;
  backExpiration: string;
  /** `dc_4_7` for the ordinary week, `dc_3_6` after a Monday holiday. Tags never pool. */
  structure: string;
}

export interface CalendarsLeg {
  legRole: string;
  occSymbol: string;
  expiration: string;
  strike: number | null;
  optionType: string;
  action: string;
  entryMid: number | null;
  status: string;
  /** `traded` or `cash_settled` — a sold leg and an expired one are different exits. */
  closeKind: string | null;
  closeValue: number | null;
}

/** One side (put or call) of one book's double calendar. Two of these make a week's structure. */
export interface CalendarsPosition {
  positionId: string;
  weekOf: string;
  entrySession: string;
  book: string;
  side: string;
  symbol: string;
  structure: string;
  frontExpiration: string;
  backExpiration: string;
  strike: number | null;
  quantity: number | null;
  entryDebit: number | null;
  entrySpot: number | null;
  entryEm: number | null;
  entryEmPct: number | null;
  entryFrontIv: number | null;
  entryBackIv: number | null;
  entryTermStructure: number | null;
  status: string;
  exitReason: string | null;
  closedSession: string | null;
  settlementSpot: number | null;
  itmSettlements: number | null;
  grossPnl: number | null;
  fees: number | null;
  /** `gross - fees`. Null if either side is unrecorded — an open week has no net, not a zero one. */
  netPnl: number | null;
  legs: CalendarsLeg[];
}

/** Per-book, per-structure results over CLOSED positions — `analytics.headline()`. */
export interface CalendarsBookCell {
  book: string;
  structure: string;
  positions: number;
  weeks: number;
  grossPnl: number | null;
  fees: number | null;
  netPnl: number | null;
  winRate: number | null;
}

/**
 * What the entry day did with its one window.
 *
 * Entry is unconditional by design, so a week with no position is never "no setup" — it is a
 * refusal, and the refusal has a name. This is the card that has to answer it.
 */
export interface CalendarsEntryWindow {
  session: string | null;
  windowStart: string | null;
  windowEnd: string | null;
  attempts: Array<{
    outcome: string;
    n: number;
    firstTs: string | null;
    lastTs: string | null;
    spot: number | null;
    em: number | null;
    putStrike: number | null;
    callStrike: number | null;
    putDebit: number | null;
    callDebit: number | null;
  }>;
  /** True when the session actually opened positions, not merely when it attempted. */
  entered: boolean;
  /** The collapsed journal's word for why the week went untraded, with its occurrence count. */
  skipReason: string | null;
  skipOccurrences: number;
  /**
   * The feed ledger for the entry session (`dc_snapshots`), summed.
   *
   * A stretch of refused rows is a feed problem and a stretch with NO rows is the loop not running
   * — without these counts those two silences look identical, which is the whole reason the module
   * keeps the table.
   */
  feed: { ticks: number; fresh: number; stale: number; spotTicks: number } | null;
}

/** Per settled week: the expected move measured at entry against the move actually realized. */
export interface CalendarsEmRow {
  weekOf: string;
  structure: string;
  expectedMove: number | null;
  realizedMove: number | null;
  ratio: number | null;
}

export interface CalendarsIntegrity {
  markCoverage: {
    session: string | null;
    marks: number;
    refused: number;
    refusalShare: number | null;
    refusals: Array<{ reason: string; n: number }>;
  };
  schemaDrift: string[];
  measurementBreaks: Array<{ date: string; key: string; note: string | null }>;
  /** `tick_cadence.json` — the mark path's resolution, which bounds how precisely a trigger replays. */
  tickCadence: { seconds: number | null; since: string | null } | null;
  dividends: Array<{ symbol: string; declaredThrough: string | null; exDates: string[]; refreshDue: boolean }>;
  /** Declared per symbol. A symbol declared as neither style is refused at entry. */
  settlement: Array<{ symbol: string; style: string | null }>;
  /** Delivered shares still held — the weekend exposure a cash-settled leg never has. */
  openShareAssignments: number;
}

export interface CalendarsPayload {
  session: string | null;
  dbPresent: boolean;
  /** The next entry's week anchors, from the module's own clock. Null if the bridge could not run. */
  plan: CalendarsWeekPlan | null;
  planError: string | null;
  currentWeek: { weekOf: string | null; positions: CalendarsPosition[] };
  entryWindow: CalendarsEntryWindow;
  openPositions: CalendarsPosition[];
  books: CalendarsBookCell[];
  emVsRealized: CalendarsEmRow[];
  integrity: CalendarsIntegrity;
  today: {
    lastIteration: { ranAt: number; phase: string; status: string; ageSeconds: number } | null;
    decisions: Array<{ book: string; reason: string; accepted: boolean; occurrences: number; lastTs: string | null }>;
  };
  params: {
    symbols: string[];
    quantity: number | null;
    emFactor: number | null;
    entryWindowStart: string | null;
    entryWindowEnd: string | null;
    exitWindowStart: string | null;
    exitWindowEnd: string | null;
    maxQuoteAgeSeconds: number | null;
    maxLegSpreadPct: number | null;
    books: Array<{ name: string; enabled: boolean }>;
    adviceEnabled: boolean;
  };
}

/** One policy's result inside one structure tag. Tags are separate buckets and never pool. */
export interface CalendarsPolicyBucket {
  structure: string;
  weeks: number;
  /** Weeks whose recorded path could answer this policy. A hole is excluded, never scored zero. */
  derivable: number;
  totalNet: number | null;
  avgNet: number | null;
  winRate: number | null;
  worst: { weekOf: string; netPnl: number } | null;
}

export interface CalendarsPolicyRow {
  policy: string;
  buckets: CalendarsPolicyBucket[];
}

/**
 * The exit-policy table and the validation it travels with.
 *
 * The module's seventh honesty rule is that no surface shows the ranking without the reason to
 * believe it, so these arrive from one call and are rendered together or not at all.
 */
export interface CalendarsPoliciesPayload {
  ok: boolean;
  error: string | null;
  weeksConsidered: number;
  caveat: string | null;
  policies: CalendarsPolicyRow[];
  validation: {
    compared: number;
    ok: boolean;
    mismatches: Array<{
      weekOf: string;
      book: string;
      derivedNet: number | null;
      realNet: number | null;
      diff: number | null;
      reason: string | null;
    }>;
  } | null;
}

/** One row per week in the ledger, per book — the history tab's index. */
export interface CalendarsWeekRow {
  weekOf: string;
  structure: string;
  entrySession: string;
  book: string;
  positions: number;
  closed: number;
  entryDebit: number | null;
  entrySpot: number | null;
  settlementSpot: number | null;
  grossPnl: number | null;
  fees: number | null;
  netPnl: number | null;
}

// ---- curve (VXX term-structure roll-yield harvest) ----
//
// Paper-only, credential-free, three books (`control`/`noflip`/`hook`) plus the advisor's synthetic
// twin. `curve_regime` is the module's second product -- one row per session, written whether or
// not any book trades -- so the payload carries it as its own series, not merely as context for a
// position. `None` never means zero, the same rule every other module's analytics layer states.

/** One open position, mirroring `analytics.worksheet()` plus its latest usable close-cost mark. */
export interface CurveOpenPosition {
  positionId: string;
  symbol: string;
  book: string;
  status: string;
  shortStrike: number | null;
  longStrike: number | null;
  expiration: string | null;
  entrySpot: number | null;
  entryCredit: number | null;
  entryWidth: number | null;
  entryMaxLoss: number | null;
  entryCreditPctOfWidth: number | null;
  entryRatio: number | null;
  entryRegime: string | null;
  entryHook: boolean;
  exposureTicks: number | null;
  /** Latest usable close-cost mark. Null means no usable mark yet -- never render it as 0. */
  currentCloseCost: number | null;
  currentSpot: number | null;
}

/** Per-book, per-symbol results over CLOSED positions -- `analytics.headline()`. */
export interface CurveBookCell {
  book: string;
  symbol: string;
  positions: number;
  grossPnl: number | null;
  fees: number | null;
  /** `gross_pnl - fees`. Null if either side is unrecorded. */
  netPnl: number | null;
  winRate: number | null;
}

/** One session's regime row -- `curve_regime`, written every session, traded or not. */
export interface CurveRegimeRow {
  tradeDate: string;
  ratio: number | null;
  regime: string | null;
  hook: boolean | null;
  vix: number | null;
  vix3m: number | null;
  usable: boolean;
  refusal: string | null;
}

/** `analytics.flip_divergence()` -- the noflip comparison's real, effective sample. */
export interface CurveFlipDivergence {
  flipDivergenceCount: number;
  controlFlipExits: number;
  note: string;
}

export interface CurveIntegrity {
  exposure: { positionsWithExposure: number; exposedTicks: number; markedTicks: number };
  markCoverage: {
    session: string | null;
    marks: number;
    refused: number;
    refusalShare: number | null;
    refusals: Array<{ reason: string; n: number }>;
  };
  /** Whether today's `curve_regime` row exists and is usable -- the series' own continuity check. */
  regimeToday: { present: boolean; usable: boolean; refusal: string | null };
  schemaDrift: string[];
  measurementBreaks: Array<{ date: string; key: string; note: string | null }>;
}

export interface CurvePayload {
  /** The resolved session every card on the page names. Null when the module has never run. */
  session: string | null;
  /** False when the store is absent -- "has not run here", which is not an error. */
  dbPresent: boolean;
  openPositions: CurveOpenPosition[];
  openCount: number;
  books: CurveBookCell[];
  flipDivergence: CurveFlipDivergence;
  /** The regime series, oldest first -- `analytics.regime_series()`. */
  regimeSeries: CurveRegimeRow[];
  integrity: CurveIntegrity;
  today: {
    lastIteration: { ranAt: number; phase: string; status: string; ageSeconds: number } | null;
  };
  params: {
    contangoMax: number | null;
    hookThreshold: number | null;
    profitTakePct: number | null;
    closeDte: number | null;
    assignmentExposureTv: number | null;
  };
}

/** One completed cycle: entry through exit. */
export interface CurveCycleRow {
  positionId: string;
  symbol: string;
  book: string;
  entrySession: string;
  closedSession: string | null;
  status: string;
  exitReason: string | null;
  shortStrike: number | null;
  longStrike: number | null;
  expiration: string | null;
  entrySpot: number | null;
  settlementSpot: number | null;
  entryCredit: number | null;
  entryWidth: number | null;
  entryRatio: number | null;
  entryRegime: string | null;
  entryHook: boolean;
  grossPnl: number | null;
  fees: number | null;
  netPnl: number | null;
}

export interface CurveMeta {
  books: string[];
  symbols: string[];
  sessions: string[];
}

// --------------------------------------------------------------------------------------------
// bwb (SPX daily-laddered put broken-wing butterfly, the 1-3-2 add-on trigger experiment).
// `None` never means zero, the same rule every other module's analytics layer states -- the
// module's own worksheet()/headline()/fire_counts()/trigger_coverage() are what each type mirrors.

/** One open position, mirroring `analytics.worksheet()`. */
export interface BwbOpenPosition {
  positionId: string;
  symbol: string;
  book: string;
  status: string;
  bodyStrike: number | null;
  nearStrike: number | null;
  farStrike: number | null;
  expiration: string | null;
  entrySpot: number | null;
  entryCredit: number | null;
  entryMaxLoss: number | null;
  /** Persisted trigger latches -- never held only in loop memory (a supervisor restart mid-session
   * must not amnesia a morning touch). */
  peakAbsDelta: number | null;
  belowFlipSeen: boolean;
  armedAt: string | null;
  addonFiredAt: string | null;
  addonCredit: number | null;
  /** Latest usable close-cost mark. Null means no usable mark yet -- never render it as 0. */
  currentCloseCost: number | null;
  currentSpot: number | null;
}

/** Per-book, per-symbol results over CLOSED positions -- `analytics.headline()`. */
export interface BwbBookCell {
  book: string;
  symbol: string;
  positions: number;
  grossPnl: number | null;
  fees: number | null;
  netPnl: number | null;
  winRate: number | null;
}

/** Per-book add-on fire counts -- `analytics.fire_counts()`. The plan's own honesty rule: the
 * real effective sample for an arm-vs-control comparison is the fire count, not the trade count --
 * until an arm's add-on fires its rows are byte-identical to control's by construction. */
export interface BwbFireCount {
  book: string;
  positions: number;
  fired: number;
  fireRate: number | null;
}

export interface BwbIntegrity {
  triggerCoverage: {
    session: string | null;
    ticks: number;
    refused: number;
    refusalShare: number | null;
    /** The two halves of `measured`, separately — see the module's `trigger_coverage()`. */
    noSpot: number;
    noFlip: number;
    /** Refusal text -> count, so the page names the input that failed. */
    reasons: Record<string, number>;
    /** Ticks recorded and NONE measured: a defect, not thin data. */
    totalFailure: boolean;
  };
  markCoverage: {
    session: string | null;
    marks: number;
    refused: number;
    refusalShare: number | null;
  };
  schemaDrift: string[];
  measurementBreaks: Array<{ date: string; key: string; note: string | null }>;
}

export interface BwbEntryAttempt {
  ts: string;
  symbol: string;
  book: string;
  outcome: string;
  credit: number | null;
}

export interface BwbManagementEvent {
  positionId: string;
  occurredAt: number;
  action: string;
  reason: string;
  executed: boolean;
  gate: string | null;
}

export interface BwbPayload {
  /** The resolved session every card on the page names. Null when the module has never run. */
  session: string | null;
  /** False when the store is absent -- "has not run here", which is not an error. */
  dbPresent: boolean;
  openPositions: BwbOpenPosition[];
  openCount: number;
  books: BwbBookCell[];
  fireCounts: BwbFireCount[];
  /** The daily-ladder correlation caveat, surfaced beside the counts per the module's own honesty
   * rule -- concurrent positions share regime context, so rows are not independent samples. */
  correlationCaveat: string;
  entryAttemptsToday: BwbEntryAttempt[];
  managementEventsToday: BwbManagementEvent[];
  integrity: BwbIntegrity;
  today: {
    lastIteration: { ranAt: number; phase: string; status: string; ageSeconds: number } | null;
  };
}

/** One completed position: entry through settlement. */
export interface BwbCycleRow {
  positionId: string;
  symbol: string;
  book: string;
  entrySession: string;
  closedSession: string | null;
  status: string;
  exitReason: string | null;
  bodyStrike: number | null;
  nearStrike: number | null;
  farStrike: number | null;
  expiration: string | null;
  entrySpot: number | null;
  entryCredit: number | null;
  armedAt: string | null;
  addonFiredAt: string | null;
  addonCredit: number | null;
  grossPnl: number | null;
  fees: number | null;
  netPnl: number | null;
}

export interface BwbMeta {
  books: string[];
  symbols: string[];
  sessions: string[];
}

/** How often MEIC's profiles reached the SAME entry decision on the same tick. */
export interface MeicDivergence {
  date: string | null;
  /** Ticks where at least two profiles both evaluated, so agreement is defined. */
  ticks: number;
  /** Share of those ticks where every evaluating profile reached the same outcome. */
  allAgreeRatePct: number | null;
  pairs: Array<{ profiles: string; ticks: number; agreementRatePct: number | null }>;
  /** The outcomes seen, most common first — what the profiles were agreeing or disagreeing ABOUT. */
  outcomes: Array<{ outcome: string; count: number }>;
}
