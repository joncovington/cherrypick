// ------------------------------------------------------------------- live MEIC (Live page, meic tab)
// MEIC's live loop, read-only (`server/src/readers/meicLive.ts`, `GET /api/live/meic`). Every field
// comes off a file the loop or its arm command wrote: the live ledger (`meic_trades.db`), the arm
// record, the orphan, hold and broker-health files beside the ledger, and the suite halt flag.

import type { MeicTradeRow } from "./modules.js";

export interface LiveMeicArm {
  /** Armed means armed FOR TODAY: the record is present and dated today's ET session. */
  armed: boolean;
  /** The session the record arms (`armed_for` in the loop's `--status`), or null with no record. */
  date: string | null;
  /** When the record was written. */
  at: string | null;
  /** A record from an earlier day: the loop's next tick deletes it, so the gate is already shut. */
  stale: boolean;
  /** `live.disarm_time` from MEIC's config, else the loop's own default (17:00 ET). */
  disarmTime: string;
  /** Past the disarm time today: an armed record is one the next tick deletes. */
  pastDisarm: boolean;
  /** The suite halt flag (`state/halt-live.flag`): blocks new ICs; closes still go through. */
  halted: boolean;
  /** `enable_live_trading` in MEIC's config; null = no readable config (unknown, never "off"). */
  liveEnabled: boolean | null;
  /** `live.symbol` -- the one symbol the loop is pinned to; null when the config does not name one. */
  symbol: string | null;
}

export interface LiveMeicOrphan {
  orderId: string | null;
  status: string | null;
  underlyingSymbol: string | null;
  externalIdentifier: string | null;
}

export interface LiveMeicHeld {
  /** Submissions the broker never answered either way, by external identifier. An unreadable hold
   *  file is reported as held -- it cannot show that nothing is. */
  unresolved: string[];
  /** Fills the broker confirmed that the ledger never recorded: external identifier -> order id. */
  unrecorded: Array<{ ext: string; orderId: string }>;
}

export interface LiveMeicBrokerHealth {
  lastOkAt: string | null;
  failingSince: string | null;
  failures: number;
  lastErrorAt: string | null;
  lastError: string | null;
}

/** One live iron condor not yet resolved: `pending` is a working entry order, not a position. */
export interface LiveMeicOpenRow {
  id: number;
  tradeDate: string;
  entryTime: string | null;
  symbol: string;
  arm: string | null;
  status: "pending" | "open" | "partial" | "partial_entry" | string;
  /** The entry order is still working at the broker: no fill yet, so no entry cash. */
  working: boolean;
  putStrike: number | null;
  callStrike: number | null;
  wingWidth: number | null;
  quantity: number | null;
  /** Net entry price per share, signed (credit +). For a working order, the limit asked. */
  netCredit: number | null;
  /** Whole-position entry cash, the trade standard's derivation; null while the entry is working. */
  entryCash: number | null;
  /** A side is closed once its close cost is recorded (`put_stop_cost` / `call_stop_cost`). */
  putHeld: boolean;
  callHeld: boolean;
  /** A close order is working on that side (`<side>_stop_fill_status = 'pending'`). */
  putClosing: boolean;
  callClosing: boolean;
  fillConfirmedAt: string | null;
  /** Dated before today's session: a 0DTE IC nothing finished (the loop's `overdue_settlement`). */
  overdue: boolean;
}

/** Totals over every resolved live row, in the standard's identities. */
export interface LiveMeicTotals {
  trades: number;
  sessions: number;
  gross: number;
  /** null (n/r) when any row never recorded its fees -- never a partial sum. */
  fees: number | null;
  /** null (n/r) when any row never recorded its settlement -- never a partial sum. */
  settlementFees: number | null;
  /** Settlement summed over the rows that recorded it, so gross − fees − this = net always holds. */
  settlementRecorded: number;
  settlementTrades: number;
  /** null (n/r) when any row never recorded its fees: that row's net is unknown, so the total is. */
  net: number | null;
  /** Rows that recorded their fees, of `trades`; `feesRecorded` and `netRecorded` cover only these. */
  feesTrades: number;
  feesRecorded: number;
  netRecorded: number;
}

export interface LiveMeicPayload {
  generatedAt: string;
  /** Today's ET session, the date every per-day gate is measured against. */
  session: string;
  /** `absent`: no live ledger on this machine; `failed`: the read threw (`ledgerError`). */
  ledger: "ok" | "absent" | "failed";
  ledgerError: string | null;
  arm: LiveMeicArm;
  /** The last orphan sweep: working broker orders no live ledger knows. `at` null = no sweep file. */
  orphans: { at: string | null; orders: LiveMeicOrphan[] };
  held: LiveMeicHeld;
  /** null when the loop has never written its broker-contact record. */
  brokerHealth: LiveMeicBrokerHealth | null;
  open: LiveMeicOpenRow[];
  /** Resolved rows (stopped / expired / force_closed), newest first, in the trade table standard. */
  history: MeicTradeRow[];
  totals: LiveMeicTotals;
}
