import path from "node:path";
import {
  LADDER_HOSPITAL_DAYS,
  type LadderLiveClosedLot,
  type LadderLiveJournalRow,
  type LadderLiveOpenLot,
  type LadderLiveOrder,
  type LadderLivePayload,
  type LadderLiveRestingOrder,
} from "@console/shared";
import type { ConsoleConfig } from "../config.js";
import { hasTable, num, readOnlyDb, str, type DatabaseHandle } from "./db.js";
import { daysBetween, etDate, ladderTradeCash } from "./ladder.js";
import { readOrphans } from "./meicLive.js";
import { EMPTY_TRADE_TOTALS, tradeTotals } from "./positionCash.js";

/**
 * The ladder's LIVE ledger, `~/.cherrypick/data/ladder/live_trades.db`, read only (`packages/ladder`,
 * `live_db.py`): the paper schema in its own file, plus the broker's columns on each lot and one
 * `ladder_orders` table. The file is absent on a machine that has never run the live loop, and that is
 * an honest empty state here (`ledger: "absent"`), apart from a read that threw (`"failed"`).
 *
 * The money is the module's own, as on the paper reader: a sold lot already adds up (entry + exit =
 * gross; gross - fees - settlement - slippage = net), so `ladderTradeCash` places it and nothing is
 * re-derived. `fees` is the buy's and the sale's together, with `fees_source` saying whether they are the
 * schedule's (`modeled`) or the broker's own (`broker`). The ledger never holds an account number, but
 * every account that reaches the wire passes `maskAccount` anyway, so a row written wrongly by something
 * else still shows `****1234`.
 *
 * Nothing here opens the file for writing, and no route built on it takes a verb.
 */

const DB_FILE = "live_trades.db";
const CLOSED_LIMIT = 200;
const ENDED_ORDERS = 50;
const JOURNAL_LIMIT = 200;

export function ladderLiveDbPath(config: ConsoleConfig): string {
  return path.join(config.paths.ladderDir, DB_FILE);
}

/** `****` and the last four characters. An already-masked value keeps its form, and nothing is returned for no value. */
export function maskAccount(v: unknown): string | null {
  if (typeof v !== "string" || v.trim() === "") return null;
  const s = v.trim();
  if (/^\*{4}[A-Za-z0-9]{0,4}$/.test(s)) return s;
  return s.length >= 4 ? `****${s.slice(-4)}` : "****";
}

const ACCOUNT_KEY = "account[_-]?number|accountNumber|account";
const JSON_FORM = new RegExp(`(["']?(?:${ACCOUNT_KEY})["']?\\s*:\\s*["'])([A-Za-z0-9-]{5,})(["'])`, "g");
const KV_FORM = new RegExp(`(\\b(?:${ACCOUNT_KEY})=)([A-Za-z0-9-]{5,})\\b`, "g");

/** `core.redact.redact_accounts`: the value of every account-naming key masked, in JSON or `key=value` form. */
export function redactAccounts(text: string): string {
  return text
    .replace(JSON_FORM, (_m, a: string, v: string, c: string) => `${a}${maskAccount(v) ?? "****"}${c}`)
    .replace(KV_FORM, (_m, a: string, v: string) => `${a}${maskAccount(v) ?? "****"}`);
}

function restingOrder(r: Record<string, unknown> | undefined): LadderLiveRestingOrder | null {
  if (r === undefined) return null;
  return {
    side: str(r["side"]) ?? "",
    state: str(r["state"]) ?? "",
    limit: num(r["limit_price"]) ?? 0,
    tif: str(r["time_in_force"]),
    final: num(r["final"]) === 1,
  };
}

function openLot(r: Record<string, unknown>, today: string, sells: Map<string, Record<string, unknown>>): LadderLiveOpenLot {
  const buySession = str(r["buy_session"]) ?? today;
  const daysHeld = Math.max(0, daysBetween(buySession, today));
  const sellClient = str(r["sell_client_id"]);
  return {
    positionId: str(r["position_id"]) ?? "",
    arm: str(r["arm"]) ?? "",
    symbol: str(r["symbol"]) ?? "",
    quantity: num(r["quantity"]) ?? 0,
    buyPrice: num(r["buy_price"]) ?? 0,
    buySession,
    buyTime: str(r["buy_time"]) ?? "",
    entryValue: num(r["entry_value"]) ?? 0,
    targetPrice: num(r["target_price"]),
    targetStage: str(r["target_stage"]),
    daysHeld,
    hospital: daysHeld > LADDER_HOSPITAL_DAYS,
    account: maskAccount(r["account"]),
    buyFees: num(r["buy_fees"]),
    feesSource: str(r["fees_source"]),
    sell: sellClient === null ? null : restingOrder(sells.get(sellClient)),
  };
}

function closedLot(r: Record<string, unknown>): LadderLiveClosedLot {
  return {
    ...ladderTradeCash(r),
    account: maskAccount(r["account"]),
    buyFees: num(r["buy_fees"]),
    sellFees: num(r["sell_fees"]),
    feesSource: str(r["fees_source"]),
  };
}

function order(r: Record<string, unknown>): LadderLiveOrder {
  return {
    clientId: str(r["client_id"]) ?? "",
    side: str(r["side"]) ?? "",
    symbol: str(r["symbol"]) ?? "",
    lotId: str(r["lot_id"]),
    shares: num(r["shares"]) ?? 0,
    limit: num(r["limit_price"]) ?? 0,
    tif: str(r["time_in_force"]),
    state: str(r["state"]) ?? "",
    final: num(r["final"]) === 1,
    account: maskAccount(r["account"]),
    placedSession: str(r["placed_session"]),
    endedSession: str(r["ended_session"]),
    filledShares: num(r["filled_shares"]),
    fillPrice: num(r["fill_price"]),
    fees: num(r["fees"]),
    feesSource: str(r["fees_source"]),
    adopted: num(r["adopted"]) === 1,
  };
}

function journalRow(r: Record<string, unknown>): LadderLiveJournalRow {
  const detail = str(r["detail"]);
  return {
    id: num(r["id"]) ?? 0,
    tradeDate: str(r["trade_date"]) ?? "",
    arm: str(r["arm"]) ?? "",
    symbol: str(r["symbol"]) ?? "",
    mode: str(r["mode"]) ?? "",
    reason: redactAccounts(str(r["reason"]) ?? ""),
    occurrences: num(r["occurrences"]) ?? 1,
    firstTs: str(r["first_ts"]),
    lastTs: str(r["last_ts"]),
    detail: detail === null ? null : redactAccounts(detail),
  };
}

function all(db: DatabaseHandle, sql: string, ...params: unknown[]): Record<string, unknown>[] {
  return db.prepare<unknown[], Record<string, unknown>>(sql).all(...params);
}

function feesBySource(rows: LadderLiveClosedLot[]): LadderLivePayload["feesBySource"] {
  const out = { modeled: 0, broker: 0, unrecorded: 0 };
  for (const r of rows) {
    if (r.feesSource === "modeled") out.modeled += 1;
    else if (r.feesSource === "broker") out.broker += 1;
    else out.unrecorded += 1;
  }
  return out;
}

type Read = Omit<LadderLivePayload, "ledger" | "ledgerError" | "today" | "orphans">;

const EMPTY: Read = {
  openLots: [],
  closedLots: [],
  closedTotals: EMPTY_TRADE_TOTALS,
  feesBySource: { modeled: 0, broker: 0, unrecorded: 0 },
  orders: [],
  journal: [],
  lastIteration: null,
};

function readLedger(db: DatabaseHandle, today: string, now: Date): Read {
  const orderRows = hasTable(db, "ladder_orders") ? all(db, "SELECT * FROM ladder_orders ORDER BY id") : [];
  const sells = new Map(orderRows.flatMap((o) => (str(o["client_id"]) === null ? [] : [[str(o["client_id"])!, o] as const])));
  const hasLots = hasTable(db, "ladder_positions");
  const openLots = hasLots
    ? all(db, "SELECT * FROM ladder_positions WHERE status = 'open' ORDER BY buy_time DESC, id DESC").map((r) => openLot(r, today, sells))
    : [];
  // The totals and the rows come from one list, so the chip cannot disagree with the table.
  const sold = hasLots ? all(db, "SELECT * FROM ladder_positions WHERE status = 'closed' ORDER BY sell_time DESC, id DESC").map(closedLot) : [];
  const orders = [...orderRows.filter((o) => num(o["final"]) !== 1), ...orderRows.filter((o) => num(o["final"]) === 1).slice(-ENDED_ORDERS).reverse()].map(order);
  const iteration = hasTable(db, "ladder_loop_iterations")
    ? all(db, "SELECT ran_at, phase, status FROM ladder_loop_iterations ORDER BY ran_at DESC LIMIT 1")[0]
    : undefined;
  const ranAt = iteration === undefined ? null : num(iteration["ran_at"]);
  return {
    openLots,
    closedLots: sold.slice(0, CLOSED_LIMIT),
    closedTotals: tradeTotals(sold),
    feesBySource: feesBySource(sold),
    orders,
    journal: hasTable(db, "ladder_decisions")
      ? all(db, `SELECT * FROM ladder_decisions ORDER BY id DESC LIMIT ${String(JOURNAL_LIMIT)}`).map(journalRow)
      : [],
    lastIteration:
      iteration === undefined || ranAt === null
        ? null
        : { ranAt, phase: str(iteration["phase"]) ?? "", status: str(iteration["status"]) ?? "", ageSeconds: Math.max(0, now.getTime() / 1000 - ranAt) },
  };
}

export function readLadderLive(config: ConsoleConfig, now: Date = new Date()): LadderLivePayload {
  const today = etDate(now);
  const dbPath = ladderLiveDbPath(config);
  const o = readOrphans(config.paths.ladderDir);
  const orphans = { at: o.at, count: o.orders.length };
  const read = readOnlyDb(dbPath, (db) => readLedger(db, today, now));
  if (read.status === "ok") return { ledger: "ok", ledgerError: null, today, orphans, ...read.value };
  return {
    ledger: read.status,
    ledgerError: read.status === "failed" ? read.error : null,
    today,
    orphans,
    ...EMPTY,
  };
}
