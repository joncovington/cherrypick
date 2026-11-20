import path from "node:path";
import {
  LADDER_HOSPITAL_DAYS,
  type LadderHistory,
  type LadderHistoryRow,
  type LadderOpenLot,
  type LadderPayload,
} from "@console/shared";
import type { ConsoleConfig } from "../config.js";
import { rangeClauses, type DateRange } from "./dateRange.js";
import { hasTable, num, str, type DatabaseHandle, withReadOnlyDb } from "./db.js";
import { EMPTY_TRADE_TOTALS, tradeTotals } from "./positionCash.js";

/**
 * cherrypick-ladder's paper ledger (`packages/ladder`), read only: one lot a row in
 * `ladder_positions`, one store and no `mode`. The money is the module's own -- a sold lot already
 * adds up (entry_value + exit_value = gross_pnl; gross_pnl - fees - settlement - slippage = net_pnl),
 * so this reads it and never re-derives it. The per-arm summary and the next buy levels are not here:
 * they come from the module's own CLI through `services/ladderBridge.ts`.
 */

const DB_FILE = "paper_trades.db";

export function ladderDbPath(config: ConsoleConfig): string {
  return path.join(config.paths.ladderDir, DB_FILE);
}

function resolveFrom(db: DatabaseHandle): string | null {
  if (!hasTable(db, "ladder_loop_iterations")) return null;
  return db.prepare<[], { d: string | null }>("SELECT MAX(session_date) AS d FROM ladder_loop_iterations").get()?.d ?? null;
}

/** The session the ladder's cards name: the loop's last run. */
export function resolveLadderSession(config: ConsoleConfig): string | null {
  return withReadOnlyDb<string | null>(ladderDbPath(config), null, resolveFrom);
}

/** Today's date in New York, the suite's session calendar whatever this machine's zone. */
export function etDate(now: Date = new Date()): string {
  return new Intl.DateTimeFormat("en-CA", { timeZone: "America/New_York", year: "numeric", month: "2-digit", day: "2-digit" }).format(now);
}

/** Whole calendar days from one ISO date to another. */
export function daysBetween(from: string, to: string): number {
  const a = Date.parse(`${from}T00:00:00Z`);
  const b = Date.parse(`${to}T00:00:00Z`);
  return Number.isNaN(a) || Number.isNaN(b) ? 0 : Math.round((b - a) / 86_400_000);
}

function round2(v: number): number {
  return Math.round(v * 100) / 100;
}

export function openLot(r: Record<string, unknown>, today: string): LadderOpenLot {
  const quantity = num(r["quantity"]) ?? 0;
  const entryValue = num(r["entry_value"]) ?? 0;
  const lastBid = num(r["last_bid"]);
  const buySession = str(r["buy_session"]) ?? today;
  const daysHeld = Math.max(0, daysBetween(buySession, today));
  const valueAtBid = lastBid === null ? null : round2(quantity * lastBid);
  return {
    positionId: str(r["position_id"]) ?? "",
    arm: str(r["arm"]) ?? "",
    symbol: str(r["symbol"]) ?? "",
    quantity,
    buyPrice: num(r["buy_price"]) ?? 0,
    buySession,
    buyTime: str(r["buy_time"]) ?? "",
    entryValue,
    targetPrice: num(r["target_price"]),
    targetStage: str(r["target_stage"]),
    lastBid,
    lastMarkTime: str(r["last_mark_time"]),
    valueAtBid,
    unrealisedAtBid: valueAtBid === null ? null : round2(valueAtBid + entryValue),
    daysHeld,
    hospital: daysHeld > LADDER_HOSPITAL_DAYS,
  };
}

/**
 * A sold lot in the trade table standard. Every figure is the module's own column: entry is the
 * purchase (-) and exit the sale (+), so entry + exit = gross, and gross less fees, settlement and
 * slippage is net. `price` is the one per-share figure, the buy, signed as the debit it is. A missing
 * cost reads null and shows `n/r`, never a zero.
 */
export function ladderTradeCash(r: Record<string, unknown>): LadderHistoryRow {
  const buyPrice = num(r["buy_price"]) ?? 0;
  return {
    positionId: str(r["position_id"]) ?? "",
    arm: str(r["arm"]) ?? "",
    symbol: str(r["symbol"]) ?? "",
    buySession: str(r["buy_session"]) ?? "",
    buyTime: str(r["buy_time"]) ?? "",
    buyPrice,
    buyLevel: num(r["buy_level"]) ?? buyPrice,
    refClose: num(r["ref_close"]),
    sellSession: str(r["sell_session"]),
    sellTime: str(r["sell_time"]),
    sellPrice: num(r["sell_price"]),
    targetPrice: num(r["target_price"]),
    exitReason: str(r["exit_reason"]),
    holdDays: num(r["hold_days"]),
    netPct: num(r["net_pct"]),
    quantity: num(r["quantity"]),
    price: -buyPrice,
    entryCash: num(r["entry_value"]),
    exitCash: num(r["exit_value"]),
    exitKind: "closed",
    grossPnl: num(r["gross_pnl"]),
    fees: num(r["fees"]),
    settlementFees: num(r["settlement"]),
    slippage: num(r["slippage"]),
    netPnl: num(r["net_pnl"]),
  };
}

export interface LadderHistoryFilter {
  arm: string | null;
  symbol?: string | null;
  range?: DateRange;
}

/** Sold lots, newest sale first. A lot's result belongs to the session it sold in, so the range
 *  bounds that, and the totals are over every lot the filter matches. */
export function readLadderHistory(config: ConsoleConfig, filter: LadderHistoryFilter): LadderHistory {
  const clauses = ["status = 'closed'"];
  const params: string[] = [];
  if (filter.arm !== null) {
    clauses.push("arm = ?");
    params.push(filter.arm);
  }
  if (filter.symbol != null) {
    clauses.push("symbol = ?");
    params.push(filter.symbol);
  }
  const range = rangeClauses("sell_session", filter.range);
  clauses.push(...range.clauses);
  params.push(...range.params);
  return withReadOnlyDb<LadderHistory>(ladderDbPath(config), { rows: [], totals: EMPTY_TRADE_TOTALS }, (db) => {
    if (!hasTable(db, "ladder_positions")) return { rows: [], totals: EMPTY_TRADE_TOTALS };
    const rows = db
      .prepare<string[], Record<string, unknown>>(
        `SELECT * FROM ladder_positions WHERE ${clauses.join(" AND ")} ORDER BY sell_time DESC, id DESC`,
      )
      .all(...params)
      .map(ladderTradeCash);
    return { rows, totals: tradeTotals(rows) };
  });
}

function emptyPayload(today: string): LadderPayload {
  return { session: null, dbPresent: false, today, openLots: [], measurementBreaks: [], lastIteration: null };
}

export function readLadder(config: ConsoleConfig, now: Date = new Date()): LadderPayload {
  const today = etDate(now);
  return withReadOnlyDb<LadderPayload>(ladderDbPath(config), emptyPayload(today), (db) => {
    const openLots = hasTable(db, "ladder_positions")
      ? db
          .prepare<[], Record<string, unknown>>("SELECT * FROM ladder_positions WHERE status = 'open' ORDER BY buy_time, id")
          .all()
          .map((r) => openLot(r, today))
      : [];
    const iteration = hasTable(db, "ladder_loop_iterations")
      ? db.prepare<[], Record<string, unknown>>("SELECT ran_at, phase, status FROM ladder_loop_iterations ORDER BY ran_at DESC LIMIT 1").get()
      : undefined;
    const ranAt = iteration === undefined ? null : num(iteration["ran_at"]);
    return {
      session: resolveFrom(db),
      dbPresent: true,
      today,
      openLots,
      measurementBreaks: hasTable(db, "measurement_breaks")
        ? db
            .prepare<[], Record<string, unknown>>("SELECT break_date, key, note FROM measurement_breaks ORDER BY break_date DESC")
            .all()
            .map((r) => ({ date: str(r["break_date"]) ?? "", key: str(r["key"]) ?? "", note: str(r["note"]) }))
        : [],
      lastIteration:
        iteration === undefined || ranAt === null
          ? null
          : {
              ranAt,
              phase: str(iteration["phase"]) ?? "",
              status: str(iteration["status"]) ?? "",
              ageSeconds: Math.max(0, now.getTime() / 1000 - ranAt),
            },
    };
  });
}
