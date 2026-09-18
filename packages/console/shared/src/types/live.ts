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
}

export interface LivePosition {
  positionId: string;
  kind: string;
  side: string;
  center: number;
  wingWidth: number;
  quantity: number;
  /** Per-contract net cash so far: positive = credit collected. */
  net: number;
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
