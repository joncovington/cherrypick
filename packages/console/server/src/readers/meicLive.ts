/**
 * The Live page's meic tab: MEIC's live loop, read-only (2026-10-15). The flies Live tab's shape,
 * cut to what MEIC's live loop records -- it keeps no per-tick marks and no decision journal of its
 * own, so there is no intraday curve and no activity feed here.
 *
 * Every figure comes off a file, never the broker and never Python:
 * - the live ledger `data/meic/meic_trades.db` (`ic_trades`), the same path every other MEIC
 *   reader here resolves for live mode (and the orchestrator's `modules.meic.live_db`);
 * - the arm record `state/meic-live-arm.json` (`core.live`: armed = present and dated today ET);
 * - beside the ledger, the loop's `live_orphans.json` (`{at, orphans}`), `live_held.json` and
 *   `live_broker_health.json` (`core.execution`'s three files), read the way `read_orphans`,
 *   `read_hold` and `read_broker_health` read them;
 * - the suite halt flag `state/halt-live.flag`, and `live.disarm_time` / `live.symbol` /
 *   `enable_live_trading` from `config/meic.json`.
 *
 * Mirrors, not derivations: the open set is `live_loop.overdue_settlement`'s status list
 * (`pending`, `open`, `partial`, `partial_entry`), a side with a recorded close cost is closed,
 * resolved rows are the three terminal statuses paper.py writes (`stopped`, `expired`,
 * `force_closed`), and money is `readers/meic.ts::meicTradeCash` -- the MEIC trade standard, pinned
 * by `meic-trade-standard` -- so this tab and the MEIC page cannot disagree about a row. Cancelled
 * rows are in neither table: an entry that never filled never held risk.
 *
 * `readOnlyDb`, not `withReadOnlyDb`: "no live ledger here" and "the read threw" are different
 * facts and the payload says which. Nothing here touches an order.
 */
import fs from "node:fs";
import path from "node:path";
import type Database from "better-sqlite3";
import { sumIfAllRecorded } from "@console/shared";
import type {
  LiveMeicArm,
  LiveMeicBrokerHealth,
  LiveMeicHeld,
  LiveMeicOpenRow,
  LiveMeicOrphan,
  LiveMeicPayload,
  LiveMeicTotals,
  MeicTradeRow,
} from "@console/shared";
import type { ConsoleConfig } from "../config.js";
import { hasColumn, hasTable, num, readJson, readOnlyDb, str } from "./db.js";
import { meicTradeCash } from "./meic.js";
import { readModuleGate } from "../services/liveLock.js";

/** `live_loop.DEFAULT_DISARM`: the loop's own default when `live.disarm_time` is unset. */
export const DEFAULT_DISARM = "17:00";

const OPEN_STATUSES = ["pending", "open", "partial", "partial_entry"] as const;
const RESOLVED_STATUSES = ["stopped", "expired", "force_closed"] as const;

const EMPTY_TOTALS: LiveMeicTotals = {
  trades: 0,
  sessions: 0,
  gross: 0,
  fees: 0,
  settlementFees: 0,
  settlementRecorded: 0,
  settlementTrades: 0,
  net: 0,
};

function round2(v: number): number {
  return Math.round(v * 100) / 100;
}

function etParts(now: Date): { session: string; minute: number } {
  const session = now.toLocaleDateString("en-CA", { timeZone: "America/New_York" });
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone: "America/New_York",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).formatToParts(now);
  const h = Number(parts.find((p) => p.type === "hour")?.value ?? "0") % 24;
  const m = Number(parts.find((p) => p.type === "minute")?.value ?? "0");
  return { session, minute: h * 60 + m };
}

/** `HH:MM` to minutes, or null for anything else (an unreadable time disarms nothing here). */
function minutesOf(hhmm: string): number | null {
  const m = /^(\d{1,2}):(\d{2})$/.exec(hhmm.trim());
  return m ? Number(m[1]) * 60 + Number(m[2]) : null;
}

/** The ledger's directory: every sibling file the loop writes sits beside `meic_trades.db`. */
export function meicLiveDbPath(config: ConsoleConfig): string {
  return path.join(config.paths.meicDir, "meic_trades.db");
}

function readArm(config: ConsoleConfig, session: string, minute: number): LiveMeicArm {
  const record = readJson(path.join(config.paths.cherrypick, "state", "meic-live-arm.json"));
  const date = str(record?.["date"]);
  const meicCfg = readJson(path.join(config.paths.cherrypick, "config", "meic.json")) ?? {};
  const live = (typeof meicCfg["live"] === "object" && meicCfg["live"] !== null ? meicCfg["live"] : {}) as Record<string, unknown>;
  const disarmTime = str(live["disarm_time"]) ?? DEFAULT_DISARM;
  const disarmMin = minutesOf(disarmTime);
  return {
    armed: date === session,
    date,
    at: str(record?.["at"]),
    stale: date !== null && date !== session,
    disarmTime,
    pastDisarm: disarmMin !== null && minute >= disarmMin,
    halted: fs.existsSync(path.join(config.paths.cherrypick, "state", "halt-live.flag")),
    liveEnabled: readModuleGate(config, "meic").liveEnabled,
    symbol: str(live["symbol"]),
  };
}

/** `core.execution.read_orphans`: the last sweep's list, [] with no readable file. */
function readOrphans(dir: string): LiveMeicPayload["orphans"] {
  const doc = readJson(path.join(dir, "live_orphans.json"));
  if (doc === null) return { at: null, orders: [] };
  const raw = Array.isArray(doc["orphans"]) ? (doc["orphans"] as unknown[]) : [];
  const orders: LiveMeicOrphan[] = raw.map((o) => {
    const r = (typeof o === "object" && o !== null ? o : {}) as Record<string, unknown>;
    const id = r["order_id"];
    return {
      orderId: id === null || id === undefined ? null : String(id),
      status: str(r["status"]),
      underlyingSymbol: str(r["underlying_symbol"]),
      externalIdentifier: str(r["external_identifier"]),
    };
  });
  return { at: str(doc["at"]), orders };
}

/** `core.execution.read_hold`: an absent file holds nothing; an unreadable one is reported held. */
function readHeld(dir: string): LiveMeicHeld {
  const p = path.join(dir, "live_held.json");
  if (!fs.existsSync(p)) return { unresolved: [], unrecorded: [] };
  const doc = readJson(p);
  if (doc === null || Array.isArray(doc)) return { unresolved: ["<unreadable hold file>"], unrecorded: [] };
  const unresolvedRaw = doc["unresolved"];
  const unresolved =
    typeof unresolvedRaw === "object" && unresolvedRaw !== null ? Object.keys(unresolvedRaw as object).sort() : [];
  const unrecordedRaw = doc["unrecorded"];
  const unrecorded =
    typeof unrecordedRaw === "object" && unrecordedRaw !== null
      ? Object.entries(unrecordedRaw as Record<string, unknown>).map(([ext, o]) => {
          const id = typeof o === "object" && o !== null ? (o as Record<string, unknown>)["order_id"] : undefined;
          return { ext, orderId: String(id ?? null) };
        })
      : [];
  return { unresolved, unrecorded };
}

/** `core.execution.read_broker_health`: the persisted broker-contact record, null with none. */
function readBrokerHealth(dir: string): LiveMeicBrokerHealth | null {
  const doc = readJson(path.join(dir, "live_broker_health.json"));
  if (doc === null || Array.isArray(doc)) return null;
  return {
    lastOkAt: str(doc["last_ok_at"]),
    failingSince: str(doc["failing_since"]),
    failures: num(doc["failures"]) ?? 0,
    lastErrorAt: str(doc["last_error_at"]),
    lastError: str(doc["last_error"]),
  };
}

/** An `ic_trades` column, or NULL on a ledger that predates it. */
function col(db: Database.Database, name: string): string {
  return hasColumn(db, "ic_trades", name) ? name : "NULL";
}

function inList(values: readonly string[]): string {
  return values.map((v) => `'${v}'`).join(", ");
}

function readLedger(db: Database.Database, session: string): { open: LiveMeicOpenRow[]; history: MeicTradeRow[]; totals: LiveMeicTotals } {
  if (!hasTable(db, "ic_trades")) return { open: [], history: [], totals: EMPTY_TOTALS };
  const money = `${col(db, "dollar_multiplier")} AS dollar_multiplier,
                 ${col(db, "settlement_fees")} AS settlement_fees,
                 ${col(db, "slippage_dollars")} AS slippage_dollars,
                 ${col(db, "put_settle_value")} AS put_settle_value,
                 ${col(db, "call_settle_value")} AS call_settle_value`;

  const open = db
    .prepare<[], Record<string, unknown>>(
      `SELECT id, trade_date, entry_time, symbol, ${col(db, "arm")} AS arm, status,
              put_strike, call_strike, wing_width, quantity, net_credit, put_credit, call_credit,
              pnl, fees, exit_reason, put_stop_cost, call_stop_cost,
              ${col(db, "put_stop_fill_status")} AS put_stop_fill_status,
              ${col(db, "call_stop_fill_status")} AS call_stop_fill_status,
              ${col(db, "fill_confirmed_at")} AS fill_confirmed_at, ${money}
         FROM ic_trades WHERE status IN (${inList(OPEN_STATUSES)}) ORDER BY trade_date DESC, id DESC`,
    )
    .all()
    .map((r): LiveMeicOpenRow => {
      const status = str(r["status"]) ?? "";
      const working = status === "pending";
      const tradeDate = str(r["trade_date"]) ?? "";
      return {
        id: num(r["id"]) ?? 0,
        tradeDate,
        entryTime: str(r["entry_time"]),
        symbol: str(r["symbol"]) ?? "",
        arm: str(r["arm"]),
        status,
        working,
        putStrike: num(r["put_strike"]),
        callStrike: num(r["call_strike"]),
        wingWidth: num(r["wing_width"]),
        quantity: num(r["quantity"]),
        netCredit: num(r["net_credit"]),
        // A working order has no fill: the credit on the row is the limit asked, not cash taken in.
        entryCash: working ? null : meicTradeCash(r).entryCash,
        putHeld: !working && num(r["put_stop_cost"]) === null,
        callHeld: !working && num(r["call_stop_cost"]) === null,
        putClosing: str(r["put_stop_fill_status"]) === "pending",
        callClosing: str(r["call_stop_fill_status"]) === "pending",
        fillConfirmedAt: str(r["fill_confirmed_at"]),
        overdue: tradeDate < session,
      };
    });

  const history = db
    .prepare<[], Record<string, unknown>>(
      `SELECT id, trade_date, entry_time, symbol, put_strike, call_strike, wing_width,
              net_credit, put_credit, call_credit, quantity, status, pnl, fees, exit_reason, iv_rank_at_entry, ${money}
         FROM ic_trades WHERE status IN (${inList(RESOLVED_STATUSES)}) ORDER BY trade_date DESC, id DESC`,
    )
    .all()
    .map(
      (r): MeicTradeRow => ({
        mode: "live",
        id: num(r["id"]) ?? 0,
        tradeDate: str(r["trade_date"]) ?? "",
        entryTime: str(r["entry_time"]),
        symbol: str(r["symbol"]) ?? "",
        putStrike: num(r["put_strike"]),
        callStrike: num(r["call_strike"]),
        wingWidth: num(r["wing_width"]),
        netCredit: num(r["net_credit"]),
        quantity: num(r["quantity"]),
        status: str(r["status"]) ?? "",
        ...meicTradeCash(r),
        exitReason: str(r["exit_reason"]),
        ivRankAtEntry: num(r["iv_rank_at_entry"]),
      }),
    );

  return { open, history, totals: totalsOf(history) };
}

/**
 * Totals summed off the rows as rendered, so the chip and the table add up the same way:
 * gross − fees − settlement = net. Settlement over any row that never recorded it is n/r, and that
 * row's settlement stays inside its fee total (the standard's rule for a split never recorded).
 */
export function totalsOf(rows: MeicTradeRow[]): LiveMeicTotals {
  let gross = 0;
  let fees = 0;
  let settlement = 0;
  let settlementTrades = 0;
  let net = 0;
  const sessions = new Set<string>();
  for (const r of rows) {
    sessions.add(r.tradeDate);
    gross += r.gross ?? 0;
    fees += r.fees ?? 0;
    if (r.settlementFees !== null) {
      settlement += r.settlementFees;
      settlementTrades += 1;
    }
    net += r.net ?? 0;
  }
  return {
    trades: rows.length,
    sessions: sessions.size,
    gross: round2(gross),
    fees: round2(fees),
    settlementFees: sumIfAllRecorded(round2(settlement), settlementTrades, rows.length),
    settlementRecorded: round2(settlement),
    settlementTrades,
    net: round2(net),
  };
}

export function readMeicLive(config: ConsoleConfig, now: Date = new Date()): LiveMeicPayload {
  const { session, minute } = etParts(now);
  const dbPath = meicLiveDbPath(config);
  const dir = path.dirname(dbPath);
  const ledger = readOnlyDb(dbPath, (db) => readLedger(db, session));
  const value = ledger.status === "ok" ? ledger.value : null;
  return {
    generatedAt: now.toISOString(),
    session,
    ledger: ledger.status,
    ledgerError: ledger.status === "failed" ? ledger.error : null,
    arm: readArm(config, session, minute),
    orphans: readOrphans(dir),
    held: readHeld(dir),
    brokerHealth: readBrokerHealth(dir),
    open: value?.open ?? [],
    history: value?.history ?? [],
    totals: value?.totals ?? EMPTY_TOTALS,
  };
}
