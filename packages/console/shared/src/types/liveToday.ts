// ------------------------------------------------------------------- live today (Live page, today tab)
// Every live loop's day on one page (`server/src/readers/liveToday.ts`, `GET /api/live/today`),
// composed from the per-loop readers: flies (`fliesLive.ts`, `flies.ts`), MEIC (`meicLive.ts`) and
// bwb (`bwb.ts`, `liveBooks.ts`). Nothing here is a new money rule; each loop's figures are its own
// reader's, and the suite line totals only what every loop states the same way.

import type { LiveMeicBrokerHealth } from "./liveMeic.js";

export type LiveLoopId = "flies" | "meic" | "bwb";

export interface LiveTodayArm {
  /** Armed means armed FOR TODAY: the loop's arm record is present and dated today's ET session. */
  armed: boolean;
  /** The session the record arms, or null with no record. */
  date: string | null;
  at: string | null;
  /** A record from an earlier day: the loop's next tick deletes it, so the gate is already shut. */
  stale: boolean;
  /** `live.disarm_time`, else the loop's own default (17:00 ET). */
  disarmTime: string;
  /** Past the disarm time today: an armed record is one the next tick deletes. */
  pastDisarm: boolean;
  /** The module's live master switch (`enable_live_trading` or `live.enabled`); null = unreadable, never "off". */
  liveEnabled: boolean | null;
  /** The configured live arm (`live.arm`), where the module has one. */
  arm: string | null;
  symbol: string | null;
}

/** Today's resolved positions, in the money layout: gross − fees − settlement = net. */
export interface LiveTodayMoney {
  /** Positions resolved today (settled / closed / stopped / expired / force-closed). */
  trades: number;
  gross: number;
  /** Trading fees, settlement excluded where it was recorded apart; null (n/r) when any of today's
   *  rows never recorded its fees -- never a partial sum. */
  fees: number | null;
  /** null (n/r) when any of today's rows never recorded settlement apart -- never a partial sum. */
  settlementFees: number | null;
  /** Settlement over the rows that recorded it, so gross − fees − this = net always holds. */
  settlementRecorded: number;
  settlementTrades: number;
  /** null (n/r) when any of today's rows never recorded its fees. */
  net: number | null;
  /** Rows that recorded their fees, of `trades`; `netRecorded` covers only these. */
  feesTrades: number;
  netRecorded: number;
  /** What "today" means for this loop's resolved rows, in a sentence. */
  basis: string;
}

export interface LiveTodayOverdue {
  session: string;
  positions: number;
  pendingEntries: number;
}

export interface LiveTodayLoop {
  loop: LiveLoopId;
  /** The loop's own Live tab, or its module page where it has none. */
  to: string;
  /** `absent`: no live ledger on this machine; `failed`: the read threw (`ledgerError`). */
  ledger: "ok" | "absent" | "failed";
  ledgerError: string | null;
  arm: LiveTodayArm;
  /** The loop's last recorded iteration, where it records one. MEIC's live loop writes none, so its
   *  state is the supervisor's `meic-live` job (one tick per run); null where neither exists. */
  loopState: { state: "live" | "idle" | "no-data"; lastAt: string | null; ageSeconds: number | null } | null;
  /** Positions holding risk now (entries filled, not resolved), any session. */
  open: number;
  /** Orders working at the broker: entries not yet filled, plus the loop's other working orders it records. */
  working: number;
  /** What `working` counts, for this loop. */
  workingBasis: string;
  /** Worst case over the open positions, whole-position dollars, negative = a loss; null where the reader does not provide one. */
  worstCase: number | null;
  worstCaseBasis: string;
  /** Entries filled today (positions dated today that held risk). */
  entriesToday: number;
  /** null when the ledger could not be read. */
  today: LiveTodayMoney | null;
  /** The loop's own intraday mark over what is open, where it records one. Never totalled: each loop marks differently. */
  mark: { value: number | null; at: string | null; basis: string } | null;
  /** The loop's daily-loss breaker, mirrored from its own `daily_loss_tripped`. `figureKind` says
   *  whether that rule sums net or gross P&L: MEIC's sums the ledger's `pnl`, which is gross. */
  breaker: { limit: number | null; figure: number | null; figureKind: "net" | "gross"; tripped: boolean | null; basis: string };
  orphans: { at: string | null; count: number };
  /** Held submissions: unresolved (no broker answer) + unrecorded (filled, not in the ledger). */
  held: number;
  brokerHealth: LiveMeicBrokerHealth | null;
  overdue: LiveTodayOverdue[];
  /** The module's `enabled` switch in the orchestrator config; null when the config names no such module. */
  moduleEnabled: boolean | null;
  /** Off: the module is disabled and the loop holds nothing -- not armed, nothing open or working, no
   *  held, orphaned or overdue item, a readable ledger. The tab shows it as one line; anything here
   *  brings the full row back. */
  retired: boolean;
}

export type LiveAttentionKind =
  | "ledger_unreadable"
  | "orphaned_orders"
  | "held_submission"
  | "daily_loss_breaker"
  | "overdue_settlement"
  | "broker_failing";

export interface LiveAttentionItem {
  loop: LiveLoopId;
  kind: LiveAttentionKind;
  detail: string;
  to: string;
}

export interface LiveTodaySuite {
  loops: number;
  armed: LiveLoopId[];
  /** Summed over the loops whose ledger was read; null when any loop's read failed (a partial total is not a total). */
  net: number | null;
  gross: number | null;
  fees: number | null;
  /** null (n/r) when any loop's settlement is n/r, or the total is null. */
  settlementFees: number | null;
  trades: number | null;
  /** Loops in the totals. */
  summed: LiveLoopId[];
  /** Loops left out of the totals, and why. */
  excluded: Array<{ loop: LiveLoopId; reason: string }>;
  /** Summed loops with a row resolved today whose fees were never recorded: net and fees are n/r. */
  feesUnrecorded: LiveLoopId[];
}

/** One hand-placed order's fills today (manage's `zero_dte.orders`). Cash is signed: a credit is +. */
export interface LiveTodayHandOrder {
  orderId: string | null;
  at: string | null;
  kind: "open" | "close" | "mixed" | "unknown";
  legs: Array<{ symbol: string; action: string; quantity: number | null; price: number | null; value: number | null }>;
  cash: number;
  /** The broker's own fee total on the order's fills (value − net value); null when a fill lacks it. */
  fees: number | null;
}

/** A 0DTE leg still held by hand, at the evaluation's mark. Value is signed, whole-position dollars. */
export interface LiveTodayHandLeg {
  symbol: string;
  right: string | null;
  strike: number | null;
  quantity: number;
  mid: number | null;
  value: number | null;
  markSource: string | null;
  markAgeSeconds: number | null;
}

/**
 * The 0DTE trades placed by hand (2026-11-03): packages/manage's `zero_dte` block, the legs the
 * ruleset's `exclude.zero_dte_underlyings` leaves out with the live loops' own fills taken away (a
 * fill is a loop's when its order id is one a loop has published). Read and shaped, never derived:
 * the split, the reconciliation and the money are manage's. Its net is shown beside the suite total,
 * never added into it: the total is every loop's realised net, and a hand trade has no ledger.
 */
export interface LiveTodayHand {
  /** ok: today's evaluation carries the view. none: no evaluation today (or ever). unread: today's
   *  evaluation could not build it (`reason`), or predates it. */
  status: "ok" | "none" | "unread";
  reason: string | null;
  /** The evaluation's session and when its snapshot was taken / last re-marked. */
  evaluatedSession: string | null;
  snapshotAt: string | null;
  markedAt: string | null;
  underlyings: string[];
  orders: LiveTodayHandOrder[];
  openLegs: LiveTodayHandLeg[];
  /** Symbols whose held quantity today's fills do not explain; while any is listed, mark/gross/net are null. */
  unreconciled: Array<{ symbol: string; held: number; loopFills: number; handFills: number }>;
  /** Hand orders filled after the loops' newest publish of their ids: possibly a loop's, not yet listed. */
  afterPublish: string[];
  /** Each loop whose published ids were read, and when it last published. */
  knownModules: Record<string, string | null>;
  loopFills: number;
  cash: number | null;
  mark: number | null;
  gross: number | null;
  fees: number | null;
  /** null while a held leg has still to settle; 0 with nothing held. */
  settlement: number | null;
  net: number | null;
  /** "at mark: ..." while legs are held, "realised: ..." when flat. */
  netBasis: string | null;
  caveats: string[];
}

export interface LiveTodayPayload {
  generatedAt: string;
  /** Today's ET session, the date every per-day gate is measured against. */
  session: string;
  /** The suite halt flag (`state/halt-live.flag`): blocks new entries on every loop. */
  halted: boolean;
  loops: LiveTodayLoop[];
  attention: LiveAttentionItem[];
  suite: LiveTodaySuite;
  /** Today's 0DTE trades placed by hand, beside the loops and outside the suite total. */
  hand: LiveTodayHand;
}
