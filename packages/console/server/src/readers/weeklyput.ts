import path from "node:path";
import type {
  TradeMoney,
  WeeklyputEntryRow,
  WeeklyputHistory,
  WeeklyputHistoryRow,
  WeeklyputMark,
  WeeklyputPayload,
  WeeklyputPosition,
} from "@console/shared";
import type { ConsoleConfig } from "../config.js";
import { rangeClauses, type DateRange } from "./dateRange.js";
import { hasTable, num, str, type DatabaseHandle, withReadOnlyDb } from "./db.js";
import { EMPTY_TRADE_TOTALS, tradeTotals } from "./positionCash.js";

/**
 * weeklyput's read layer (packages/weeklyput): item 57's unhedged short put, paper only, one store
 * and no `mode`. The money is the module's own -- each settled put already adds up (entry_value +
 * exit_value = gross_pnl; gross_pnl - fees - settlement - slippage = net_pnl), so this reads it and
 * never re-derives it. What the module is JUDGED on, the per-cycle equal-weight mean of net in bp,
 * is not here either: it comes from the module's own `cycles` and `summary` through
 * `services/weeklyputBridge.ts`.
 */

const DB_FILE = "paper_trades.db";

function dbPath(config: ConsoleConfig): string {
  return path.join(config.paths.weeklyputDir, DB_FILE);
}

function round2(v: number): number {
  return Math.round(v * 100) / 100;
}

/** The latest mark of each open put that gave a usable quote. */
function latestMarks(db: DatabaseHandle): Map<string, WeeklyputMark> {
  const out = new Map<string, WeeklyputMark>();
  if (!hasTable(db, "weeklyput_marks")) return out;
  for (const r of db
    .prepare<[], Record<string, unknown>>(
      `SELECT m.* FROM weeklyput_marks m
         JOIN (SELECT position_id, MAX(ts) AS ts FROM weeklyput_marks WHERE usable = 1 GROUP BY position_id) last
           ON last.position_id = m.position_id AND last.ts = m.ts
        WHERE m.usable = 1`,
    )
    .all()) {
    out.set(str(r["position_id"]) ?? "", {
      ts: str(r["ts"]) ?? "",
      mid: num(r["mid"]),
      value: num(r["mark_value"]),
      spot: num(r["spot"]),
      extrinsic: num(r["extrinsic"]),
    });
  }
  return out;
}

/** The newest spot any mark of the put recorded, usable or not: a refused quote still carries one. */
function latestSpots(db: DatabaseHandle): Map<string, number> {
  const out = new Map<string, number>();
  if (!hasTable(db, "weeklyput_marks")) return out;
  for (const r of db
    .prepare<[], Record<string, unknown>>(
      `SELECT m.position_id, m.spot FROM weeklyput_marks m
         JOIN (SELECT position_id, MAX(ts) AS ts FROM weeklyput_marks WHERE spot IS NOT NULL GROUP BY position_id) last
           ON last.position_id = m.position_id AND last.ts = m.ts`,
    )
    .all()) {
    const spot = num(r["spot"]);
    if (spot !== null) out.set(str(r["position_id"]) ?? "", spot);
  }
  return out;
}

function position(r: Record<string, unknown>, marks: Map<string, WeeklyputMark>, spots: Map<string, number>): WeeklyputPosition {
  const id = str(r["position_id"]) ?? "";
  const status = str(r["status"]) ?? "";
  const mark = status === "open" ? (marks.get(id) ?? null) : null;
  // The spot a card measures distance from: the usable mark's, else the newest recorded one.
  const spot = mark?.spot ?? (status === "open" ? (spots.get(id) ?? null) : null);
  return {
    positionId: id,
    arm: str(r["arm"]) ?? "",
    symbol: str(r["symbol"]) ?? "",
    status,
    expiration: str(r["expiration"]) ?? "",
    entrySession: str(r["entry_session"]) ?? "",
    entryTime: str(r["entry_time"]),
    strike: num(r["strike"]) ?? 0,
    quantity: num(r["quantity"]) ?? 1,
    settlementStyle: str(r["settlement_style"]) ?? "",
    entrySpot: num(r["entry_spot"]),
    entryForward: num(r["entry_forward"]) ?? 0,
    entryDelta: num(r["entry_delta"]),
    entryBid: num(r["entry_bid"]),
    entryAsk: num(r["entry_ask"]),
    entryMid: num(r["entry_mid"]),
    entryValue: num(r["entry_value"]) ?? 0,
    entryFees: num(r["entry_fees"]) ?? 0,
    entrySlippage: num(r["entry_slippage"]) ?? 0,
    notional: num(r["notional"]) ?? 0,
    settleSession: str(r["settle_session"]),
    settlePrice: num(r["settle_price"]),
    exitValue: num(r["exit_value"]),
    exitReason: str(r["exit_reason"]),
    settlement: num(r["settlement"]),
    grossPnl: num(r["gross_pnl"]),
    fees: num(r["fees"]),
    slippage: num(r["slippage"]),
    netPnl: num(r["net_pnl"]),
    netBp: num(r["net_bp"]),
    assignmentExposedAt: str(r["assignment_exposed_at"]),
    mark: mark === null && spot === null ? null : { ts: mark?.ts ?? "", mid: mark?.mid ?? null, value: mark?.value ?? null, spot, extrinsic: mark?.extrinsic ?? null },
  };
}

function entryRow(r: Record<string, unknown>): WeeklyputEntryRow {
  return {
    arm: str(r["arm"]) ?? "",
    symbol: str(r["symbol"]) ?? "",
    expiration: str(r["expiration"]) ?? "",
    entrySession: str(r["entry_session"]) ?? "",
    outcome: str(r["outcome"]) ?? "",
    refusal: str(r["refusal"]),
    positionId: str(r["position_id"]),
    decidedAt: str(r["decided_at"]),
  };
}

/** How a settled put ended. The module writes `expired`, `settled` (SPX in the money) or `assigned`. */
function exitKind(reason: string | null, status: string): TradeMoney["exitKind"] {
  if (status !== "closed") return null;
  return reason === "expired" || reason === "settled" || reason === "assigned" ? reason : "closed";
}

/**
 * A settled put in the trade table standard. Every figure is the module's own column: entry is the
 * credit (+) and exit the intrinsic paid (-, or 0), so entry + exit = gross; net is `net_pnl`
 * (gross - fees - settlement - slippage). Fees, settlement and slippage are three columns in the
 * ledger already, so nothing is split here. A missing cost reads null, which renders `n/r`, and
 * poisons net rather than being taken as free.
 */
export function weeklyputTradeCash(r: Record<string, unknown>): WeeklyputHistoryRow {
  const quantity = num(r["quantity"]) ?? 1;
  const entryCash = num(r["entry_value"]);
  const mid = num(r["entry_mid"]);
  return {
    positionId: str(r["position_id"]) ?? "",
    arm: str(r["arm"]) ?? "",
    symbol: str(r["symbol"]) ?? "",
    expiration: str(r["expiration"]) ?? "",
    entrySession: str(r["entry_session"]) ?? "",
    settleSession: str(r["settle_session"]),
    strike: num(r["strike"]) ?? 0,
    entryDelta: num(r["entry_delta"]),
    settlePrice: num(r["settle_price"]),
    netBp: num(r["net_bp"]),
    exitReason: str(r["exit_reason"]),
    quantity,
    // The one per-share figure: the put sold at mid, a credit.
    price: mid,
    entryCash: entryCash === null ? null : round2(entryCash),
    exitCash: num(r["exit_value"]),
    exitKind: exitKind(str(r["exit_reason"]), str(r["status"]) ?? ""),
    grossPnl: num(r["gross_pnl"]),
    fees: num(r["fees"]),
    settlementFees: num(r["settlement"]),
    slippage: num(r["slippage"]),
    netPnl: num(r["net_pnl"]),
  };
}

export interface WeeklyputHistoryFilter {
  arm: string | null;
  symbol?: string | null;
  range?: DateRange;
}

/** Settled puts, newest expiry first. A put's result belongs to its expiry, so the range bounds
 *  that, and the totals are over every put the filter matches. */
export function readWeeklyputHistory(config: ConsoleConfig, filter: WeeklyputHistoryFilter): WeeklyputHistory {
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
  const range = rangeClauses("expiration", filter.range);
  clauses.push(...range.clauses);
  params.push(...range.params);
  return withReadOnlyDb<WeeklyputHistory>(dbPath(config), { rows: [], totals: EMPTY_TRADE_TOTALS }, (db) => {
    if (!hasTable(db, "weeklyput_positions")) return { rows: [], totals: EMPTY_TRADE_TOTALS };
    const rows = db
      .prepare<string[], Record<string, unknown>>(
        `SELECT * FROM weeklyput_positions WHERE ${clauses.join(" AND ")} ORDER BY expiration DESC, symbol, arm`,
      )
      .all(...params)
      .map(weeklyputTradeCash);
    return { rows, totals: tradeTotals(rows) };
  });
}

function emptyPayload(): WeeklyputPayload {
  return { session: null, dbPresent: false, positions: [], entries: [], measurementBreaks: [], lastIteration: null };
}

export function readWeeklyput(config: ConsoleConfig): WeeklyputPayload {
  return withReadOnlyDb<WeeklyputPayload>(dbPath(config), emptyPayload(), (db) => {
    const session = resolveFrom(db);
    const marks = latestMarks(db);
    const spots = latestSpots(db);
    const positions = hasTable(db, "weeklyput_positions")
      ? db
          .prepare<[], Record<string, unknown>>("SELECT * FROM weeklyput_positions ORDER BY expiration DESC, symbol, arm")
          .all()
          .map((r) => position(r, marks, spots))
      : [];
    const entries = hasTable(db, "weeklyput_entries")
      ? db
          .prepare<[], Record<string, unknown>>("SELECT * FROM weeklyput_entries ORDER BY expiration DESC, symbol, arm")
          .all()
          .map(entryRow)
      : [];
    const iteration = hasTable(db, "weeklyput_loop_iterations")
      ? db
          .prepare<[], Record<string, unknown>>(
            "SELECT ran_at, phase, status FROM weeklyput_loop_iterations ORDER BY ran_at DESC LIMIT 1",
          )
          .get()
      : undefined;
    const ranAt = iteration === undefined ? null : num(iteration["ran_at"]);
    return {
      session,
      dbPresent: true,
      positions,
      entries,
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
              ageSeconds: Math.max(0, Date.now() / 1000 - ranAt),
            },
    };
  });
}

function resolveFrom(db: DatabaseHandle): string | null {
  if (!hasTable(db, "weeklyput_loop_iterations")) return null;
  return db.prepare<[], { d: string | null }>("SELECT MAX(session_date) AS d FROM weeklyput_loop_iterations").get()?.d ?? null;
}

/** The session every weeklyput card names: the loop's last run. */
export function resolveWeeklyputSession(config: ConsoleConfig): string | null {
  return withReadOnlyDb<string | null>(dbPath(config), null, resolveFrom);
}

/** The ledger file, for the bridge's `--db`. */
export function weeklyputDbPath(config: ConsoleConfig): string {
  return dbPath(config);
}
