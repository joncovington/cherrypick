/**
 * The Live page (2026-09-17): the flies live pilot's day, read-only.
 *
 * Two honesty rules the page states on its face. A mark is a MID, not a fill -- `markPnl` and the
 * intraday curve come from `fly_live_marks`, which the live loop writes off the cached leg mids
 * every tick. And the period tiles are SETTLED net only -- flies settles at expiry, so today's
 * settled figure is zero until the bell; the intraday number is the mark, labelled as such.
 */

export type LiveLedgerState = "ok" | "absent" | "failed";

export interface LivePeriod {
  /** Settled net (gross P&L less fees) over rows settled in the period -- core.ledgers' flies rule. */
  net: number;
  trades: number;
  sessions: number;
  /** The paper control's same-period settled net, for the pilot's paper-vs-live comparison. */
  paperNet: number | null;
  /** Return on session peak risk over the period's finished sessions that recorded a peak: see
   *  LiveRiskSession. Not the buying-power cap (`live.max_open_margin_dollars`), which is a limit. */
  onRisk: LiveOnRisk;
}

/**
 * One finished-or-running session's settled net against the most it had at stake at once: the
 * largest `fly_live_marks.open_margin` of the day, the loop's own open worst-case exposure (the
 * figure the buying-power cap reads). A completed fly that can no longer lose drops out of it, so
 * a day that recycles the same budget can return more than 100%.
 */
export interface LiveRiskSession {
  session: string;
  trades: number;
  net: number;
  /** Null before `fly_live_marks` existed (2026-09-17) -- not recorded, never zero. */
  peakRisk: number | null;
  peakAt: string | null;
  /** net / peakRisk, only once nothing is left open (a running day's settled net is not its result). */
  onRisk: number | null;
  /** Nothing open or working: the day's settled net is final. */
  complete: boolean;
}

/** Σ net / Σ daily peak risk over the sessions that have both, and how many of the sessions did. */
export interface LiveOnRisk {
  net: number;
  peakRisk: number;
  ratio: number | null;
  /** Sessions the ratio covers, out of `of` settled sessions in scope. */
  sessions: number;
  of: number;
}

export interface LiveCompletion {
  leggedEntries: number;
  completed: number;
  completionRatePct: number | null;
  neverOffered: number;
  bufferBlocked: number;
  floorBlocked: number;
  unknown: number;
  medianLatencyMin: number | null;
  minLatencyMin: number | null;
  maxLatencyMin: number | null;
  medianSpotMove: number | null;
}

export interface LiveVsPaperSide {
  sessions: number;
  entries: number;
  completed: number;
  completionRatePct: number | null;
  medianLatencyMin: number | null;
  avgCredit: number | null;
}

/**
 * The flies Performance tab's figures for the live pilot's arm, every session through the one
 * shown -- `readFliesPerformance` in live mode, carried rather than recomputed, so the two pages
 * cannot disagree -- plus the per-session return on session peak risk, which that tab does not have.
 */
export interface LivePerformance {
  arm: string | null;
  tiles: {
    trades: number;
    sessions: number;
    netPnl: number;
    winRatePct: number | null;
    profitFactor: number | null;
    feeDragPct: number | null;
    completionRatePct: number | null;
  };
  risk: {
    sharpe: number | null;
    sortino: number | null;
    calmar: number | null;
    recoveryFactor: number | null;
    sampleSize: number;
    undersampledFlag: boolean;
    sharpeOverfitFlag: boolean;
  };
  maxDrawdown: number | null;
  equity: Array<{ date: string; netPnl: number; equity: number; drawdown: number }>;
  completion: LiveCompletion;
  completionTrend: Array<{ day: string; legged: number; completed: number; ratePct: number | null }>;
  liveVsPaper: {
    arm: string;
    live: LiveVsPaperSide;
    paper: LiveVsPaperSide;
    completionGapPct: number | null;
    abort: { minLiveEntries: number; gapLimitPct: number; armed: boolean; triggered: boolean };
  } | null;
  onRisk: LiveOnRisk;
  /** Newest first. */
  sessions: LiveRiskSession[];
  /**
   * The flies "performance" slide's calibration reading (`core.metrics`, via the console's metrics
   * bridge) run against the LIVE ledger for this arm, in the suite's current evidence window.
   * snake_case keys verbatim, as that slide reads them. Null reading with an error when the bridge
   * refused; null reading with no error when the arm has no closed live trades in the window.
   */
  calibration: { reading: Record<string, unknown> | null; from: string | null; error: string | null };
}

export interface LivePosition {
  positionId: string;
  kind: string;
  side: string;
  center: number;
  wingWidth: number;
  quantity: number;
  /** Per-contract net cash so far: positive = credit collected. A PRICE, so not `net` -- which on
   *  LivePeriod above, and across the console, means P&L after fees. */
  netCredit: number;
  fees: number;
  floorDollars: number | null;
  riskFree: boolean;
  status: string;
  entryTime: string | null;
  entryFillStatus: string | null;
  completionFillStatus: string | null;
  completedAt: string | null;
  /** Latest mid mark this tick wrote for the row, or null when no leg quote was usable. */
  mark: { at: string; structureMid: number; markPnl: number; restingLimit: number | null } | null;
  pnl: number | null;
}

export interface LiveFeedRow {
  mode: string;
  reason: string;
  accepted: boolean;
  firstSeen: string | null;
  lastSeen: string | null;
  occurrences: number;
  centerLast: number | null;
  detail: string | null;
}

export interface LivePoint {
  /** Minutes since midnight ET, the session clock every hand-SVG chart here draws on. */
  m: number;
  v: number;
}

export interface LiveGap {
  m: number;
  reason: string;
}

export interface LiveAccount {
  account: string;
  netLiquidatingValue: number | null;
  derivativeBuyingPower: number | null;
  usedDerivativeBuyingPower: number | null;
  /** Whole-account figures from the broker's own positions, every module and manual trade included. */
  value: number | null;
  openPl: number | null;
  dayPl: number | null;
  legCount: number | null;
  unpricedCount: number | null;
  at: string;
}

export interface LiveFliesPayload {
  generatedAt: string;
  session: string;
  /**
   * Why this session: `requested` (?session=), `today` (the session has opened), or
   * `last_completed` -- before today's open, the last session the pilot traded, so the page reads
   * the numbers it just finished rather than an empty day.
   */
  sessionBasis: "requested" | "today" | "last_completed";
  ledger: LiveLedgerState;
  ledgerError: string | null;
  arm: {
    armed: boolean;
    date: string | null;
    stale: boolean;
    halted: boolean;
    liveEnabled: boolean | null;
    arm: string | null;
    symbol: string | null;
  };
  loop: { state: "live" | "idle" | "no-data"; ageSeconds: number | null; lastIterationAt: string | null };
  periods: { today: LivePeriod; week: LivePeriod; month: LivePeriod; year: LivePeriod };
  today: {
    positions: number;
    open: number;
    pending: number;
    completionPct: number | null;
    fees: number;
    /** Every open position's own worst case, summed (negative = a real loss is still possible). */
    maxPossibleLoss: number;
    /**
     * The session's largest worst case at expiry, held after the book settles until the next
     * session opens (null while it cannot apply). Same figure as the flies session tab's.
     */
    sessionPeakWorst: { worst: number; at: string } | null;
    /** The latest tick's summed mid mark across open positions, and when it was taken. */
    markPnl: number | null;
    markedAt: string | null;
    /** Peak-to-trough of the summed mark path so far today, as a positive dollar figure. */
    maxDrawdown: number | null;
    peakMarkPnl: number | null;
  };
  buyingPower: {
    /** The loop's own gate: open worst-case exposure against live.max_open_margin_dollars. */
    open: number;
    cap: number | null;
    /** From the broker, via the orchestrator's positions verb; null when unavailable. */
    account: LiveAccount | null;
    accountError: string | null;
  };
  positions: LivePosition[];
  feed: LiveFeedRow[];
  performance: LivePerformance;
  series: {
    /** SPX % change from `spxBaseline`. */
    spx: LivePoint[];
    spxBaseline: { value: number; source: "prev_close" | "first_tick" } | null;
    vix: LivePoint[];
    vix3m: LivePoint[];
    markPnl: LivePoint[];
    openMargin: LivePoint[];
    /** Ticks the loop refused to price, with the feed's own reason. */
    gaps: LiveGap[];
  };
}
