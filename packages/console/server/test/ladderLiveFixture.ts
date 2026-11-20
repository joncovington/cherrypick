import fs from "node:fs";
import path from "node:path";
import Database from "better-sqlite3";

/**
 * A ladder LIVE ledger for tests: the module's own schema (`fixtures/ladder-live-ledger-schema.sql`,
 * generated from `live_db.connect`) filled with invented lots, orders and journal rows. Nothing here is
 * a real account or a real fill. The account is a made-up string so a leak of it is findable.
 */

export const FAKE_ACCOUNT = "5WZ04242";
export const FAKE_MASKED = "****4242";

const SCHEMA = fs.readFileSync(path.join(__dirname, "fixtures", "ladder-live-ledger-schema.sql"), "utf-8");

export function insert(db: Database.Database, table: string, row: Record<string, unknown>): void {
  const cols = Object.keys(row);
  db.prepare(`INSERT INTO ${table} (${cols.join(",")}) VALUES (${cols.map(() => "?").join(",")})`).run(...cols.map((c) => row[c]));
}

/** An open lot; override anything. Whole-position cash: `entry_value` is the purchase, negative. */
export function openLot(db: Database.Database, id: string, over: Record<string, unknown> = {}): void {
  insert(db, "ladder_positions", {
    position_id: id, arm: "session", symbol: "TQQQ", entry_rule: "session", target_rule: "fixed", quantity: 20,
    buy_level: 50, buy_price: 50, buy_session: "2026-10-15", buy_time: "2026-10-15T09:05:00-04:00", status: "open",
    target_price: 52, target_stage: "gain", entry_value: -1000, account: FAKE_ACCOUNT, buy_fees: 0.02, fees_source: "modeled",
    ...over,
  });
}

/** A sold lot that adds up: entry + exit = gross; gross - fees = net (settlement and slippage are zero for shares). */
export function soldLot(db: Database.Database, id: string, over: Record<string, unknown> = {}): void {
  insert(db, "ladder_positions", {
    position_id: id, arm: "session", symbol: "TQQQ", entry_rule: "session", target_rule: "fixed", quantity: 20,
    buy_level: 48, buy_price: 48, buy_session: "2026-10-14", buy_time: "2026-10-14T09:10:00-04:00", status: "closed",
    target_price: 49.92, target_stage: "gain", sell_price: 49.92, sell_session: "2026-10-15", sell_time: "2026-10-15T10:30:00-04:00",
    exit_reason: "target_gain", hold_days: 1, entry_value: -960, exit_value: 998.4, gross_pnl: 38.4, fees: 0.07, settlement: 0,
    slippage: 0, net_pnl: 38.33, net_pct: 3.99, account: FAKE_ACCOUNT, buy_fees: 0.02, sell_fees: 0.05, fees_source: "broker",
    ...over,
  });
}

export function order(db: Database.Database, clientId: string, over: Record<string, unknown> = {}): void {
  insert(db, "ladder_orders", {
    client_id: clientId, order_id: `B${clientId.length}${Math.abs(clientId.charCodeAt(clientId.length - 1))}`, side: "sell", symbol: "TQQQ",
    lot_id: null, shares: 20, limit_price: 52, time_in_force: "GTC_EXT", account: FAKE_ACCOUNT, state: "working", final: 0,
    placed_session: "2026-10-15", ...over,
  });
}

export function journal(db: Database.Database, over: Record<string, unknown> = {}): void {
  insert(db, "ladder_decisions", {
    trade_date: "2026-10-15", arm: "session", symbol: "TQQQ", mode: "dry_run", reason: "would place buy TQQQ ldr.b.TQQQ.20261015.2",
    accepted: 1, occurrences: 1, ...over,
  });
}

/** An empty live ledger, schema only: the state of a loop that has ticked but never traded. */
export function emptyLiveLedger(dir: string): Database.Database {
  fs.mkdirSync(dir, { recursive: true });
  const db = new Database(path.join(dir, "live_trades.db"));
  db.exec(SCHEMA);
  return db;
}

/** A ledger with two held lots (one past 18 days, unsold), two sold, orders and a journal. */
export function populatedLiveLedger(dir: string): Database.Database {
  const db = emptyLiveLedger(dir);
  openLot(db, "session:TQQQ:2026-10-15:1", { sell_client_id: "ldr.s.TQQQ.20261015.1" });
  openLot(db, "session:SOXL:2026-09-20:1", { symbol: "SOXL", buy_price: 40, buy_level: 40, buy_session: "2026-09-20", buy_time: "2026-09-20T09:40:00-04:00", quantity: 10, entry_value: -400, target_price: 41.6, buy_fees: 0.01 });
  soldLot(db, "session:TQQQ:2026-10-14:1");
  soldLot(db, "session:TQQQ:2026-10-01:1", {
    quantity: 10, buy_price: 45, buy_level: 45, buy_session: "2026-10-01", buy_time: "2026-10-01T09:30:00-04:00", sell_price: 46.8,
    sell_session: "2026-10-02", sell_time: "2026-10-02T11:00:00-04:00", hold_days: 1, entry_value: -450, exit_value: 468, gross_pnl: 18,
    fees: 0.04, net_pnl: 17.96, net_pct: 3.99, sell_fees: 0.02, fees_source: "modeled",
  });
  order(db, "ldr.s.TQQQ.20261015.1", { lot_id: "session:TQQQ:2026-10-15:1", order_id: "9001" });
  order(db, "ldr.b.TQQQ.20261015.2", { side: "buy", limit_price: 48.5, lot_id: null, order_id: "9002", shares: 20 });
  order(db, "ldr.b.TQQQ.20261015.1", { side: "buy", limit_price: 50, state: "filled", final: 1, filled_shares: 20, fill_price: 50, ended_session: "2026-10-15", order_id: "9000", fees: 0.02, fees_source: "modeled" });
  journal(db);
  journal(db, { mode: "alert", reason: "lot_past_days session:SOXL:2026-09-20:1", accepted: 0, detail: JSON.stringify({ account_number: FAKE_ACCOUNT, days: 25 }) });
  journal(db, { mode: "refused", reason: `place buy TQQQ failed account=${FAKE_ACCOUNT}`, accepted: 0, occurrences: 3 });
  insert(db, "ladder_loop_iterations", { ran_at: Math.floor(Date.parse("2026-10-15T15:59:00Z") / 1000), session_date: "2026-10-15", phase: "session", status: "ok" });
  return db;
}
