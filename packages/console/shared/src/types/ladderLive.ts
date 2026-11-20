import type { LadderHistoryRow } from "./ladder.js";
import type { TradeTotals } from "./modules.js";

// ------------------------------------------------------------------- the ladder's live view
// Read-only. The gates, the mode, the arm and the standing intents are the module's own
// (`python -m cherrypick.ladder.live_loop --status`, `services/ladderLiveBridge.ts`); the lots, the
// orders and the journal are `live_trades.db` (`readers/ladderLive.ts`). Nothing here arms, disarms,
// halts or places anything, and no account is ever stored unmasked.

/** One of the nine live gates, as the module words it. `blocks` says what a failed gate stops. */
export interface LadderLiveGate {
  name: string;
  ok: boolean;
  reason: string;
  /** `all`: the loop may do nothing on the account. `new_buys`: new buys stop, held lots keep their sells. */
  blocks: "all" | "new_buys";
}

/** An order the dry-run loop would send, standing since its terms last changed. */
export interface LadderLiveIntent {
  what: string;
  reason: string | null;
  lot: string | null;
  shares: number | null;
  limit: number | null;
  tif: string | null;
}

/** The ledger's open shares in one fund against the broker's, from the last tick's cross-check. */
export interface LadderLiveFundCheck {
  fund: string;
  ledger: number | null;
  broker: number | null;
  /** `match`, `shortfall` or `surplus`. */
  status: string | null;
  suspectedSplit: string | null;
}

export interface LadderLiveChecks {
  at: string | null;
  date: string | null;
  /** False: the broker's positions could not be read on the last tick, so no fund bought. */
  positionsReadable: boolean | null;
  funds: LadderLiveFundCheck[];
  /** Funds whose new buys are stopped, and why. */
  blocked: Array<{ fund: string; reason: string }>;
  splitsDeclared: Array<{ fund: string; exDate: string; ratio: string }>;
  splitProblems: string[];
  intents: LadderLiveIntent[];
}

/** `live_loop --status`, normalised. `ok: false` is a bridge failure, never a "not ready" verdict. */
export interface LadderLiveStatus {
  ok: boolean;
  error: string | null;
  /** `live` only when `live.dry_run` is exactly false. */
  mode: "dry-run" | "live" | null;
  note: string | null;
  today: string | null;
  ready: boolean | null;
  newBuysAllowed: boolean | null;
  armedToday: boolean | null;
  armedFor: string | null;
  /** The designated account, masked (`****1234`). */
  account: string | null;
  haltFlag: boolean | null;
  unmetGates: string[];
  gates: LadderLiveGate[];
  /** What an uncertain submit still holds: submissions with no broker answer, and fills the ledger lacks. */
  held: { unresolved: string[]; unrecorded: number };
  /** The last tick's checks; null before the loop has ticked. */
  checks: LadderLiveChecks | null;
}

/** The resting sell (or buy) an open lot is waiting on. */
export interface LadderLiveRestingOrder {
  side: string;
  state: string;
  limit: number;
  tif: string | null;
  final: boolean;
}

export interface LadderLiveOpenLot {
  positionId: string;
  arm: string;
  symbol: string;
  quantity: number;
  buyPrice: number;
  buySession: string;
  buyTime: string;
  /** Whole-position cash paid, negative. */
  entryValue: number;
  targetPrice: number | null;
  targetStage: string | null;
  daysHeld: number;
  /** Held past `LADDER_HOSPITAL_DAYS`. */
  hospital: boolean;
  /** The designated account, masked. */
  account: string | null;
  /** The buy's fee so far, and where it came from (`modeled` until the broker's own rows are read). */
  buyFees: number | null;
  feesSource: string | null;
  /** The lot's sell order at the broker, where the ledger has one. */
  sell: LadderLiveRestingOrder | null;
}

/** A sold live lot in the trade table standard, with where its fees came from. */
export interface LadderLiveClosedLot extends LadderHistoryRow {
  account: string | null;
  buyFees: number | null;
  sellFees: number | null;
  /** `modeled` (the schedule) or `broker` (the broker's own transaction rows); null when never recorded. */
  feesSource: string | null;
}

export interface LadderLiveOrder {
  clientId: string;
  side: string;
  symbol: string;
  lotId: string | null;
  shares: number;
  limit: number;
  tif: string | null;
  state: string;
  /** The broker has ended it: it never changes again. */
  final: boolean;
  account: string | null;
  placedSession: string | null;
  endedSession: string | null;
  filledShares: number | null;
  fillPrice: number | null;
  fees: number | null;
  feesSource: string | null;
  /** Found at the broker rather than placed by this loop. */
  adopted: boolean;
}

/** One journal row: an alert, a refusal, a blocked buy, a split, or a dry-run intent. */
export interface LadderLiveJournalRow {
  id: number;
  tradeDate: string;
  arm: string;
  symbol: string;
  /** `alert`, `refused`, `blocked`, `dry_run`, `split`, `positions` or `order`. */
  mode: string;
  reason: string;
  occurrences: number;
  firstTs: string | null;
  lastTs: string | null;
  /** The row's detail as the module wrote it, accounts masked; null when it wrote none. */
  detail: string | null;
}

export interface LadderLivePayload {
  /** `absent`: no live ledger on this machine (the usual state); `failed`: the read threw. */
  ledger: "ok" | "absent" | "failed";
  ledgerError: string | null;
  today: string;
  openLots: LadderLiveOpenLot[];
  closedLots: LadderLiveClosedLot[];
  /** Every sold live lot, not only the page of rows: gross − fees − settlement − slippage = net. */
  closedTotals: TradeTotals;
  /** Sold lots whose fees are from the schedule, and from the broker's own rows. */
  feesBySource: { modeled: number; broker: number; unrecorded: number };
  /** Working orders first, then the most recent ended ones. */
  orders: LadderLiveOrder[];
  journal: LadderLiveJournalRow[];
  /** The last orphan sweep: working broker orders carrying a ladder client id the ledger never recorded. */
  orphans: { at: string | null; count: number };
  /** The loop's last tick; null with none. */
  lastIteration: { ranAt: number; phase: string; status: string; ageSeconds: number } | null;
}
