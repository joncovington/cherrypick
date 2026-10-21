import { afterEach, beforeEach, describe, expect, it } from "vitest";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import Database from "better-sqlite3";
import Fastify from "fastify";
import type { ConsoleConfig } from "../src/config.js";
import { registerSecurity } from "../src/security.js";
import { registerLiveRoutes } from "../src/routes/live.js";
import { readLiveToday } from "../src/readers/liveToday.js";
import { closePooledDbs } from "../src/readers/db.js";

/**
 * The Live page's `today` tab composes the per-loop readers into one payload. What is pinned: each
 * loop's money obeys gross − fees − settlement = net with settlement n/r (never zero) where a row
 * never recorded it; the arm record is armed only when dated today and before the disarm time; every
 * attention item fires from its own file or ledger state and nothing fires on a quiet day; the suite
 * total sums only loops whose ledger was read, and goes n/r rather than partial when one failed; an
 * absent ledger is not a failed one.
 */

// 12:00 ET on 2026-10-15.
const NOON = new Date("2026-10-15T16:00:00Z");
const DAY = "2026-10-15";

let root: string;
let config: ConsoleConfig;

function cfg(r: string): ConsoleConfig {
  for (const d of ["state", "config", "flies", "meic", "bwb", "gex"]) fs.mkdirSync(path.join(r, d), { recursive: true });
  fs.writeFileSync(path.join(r, "config.json"), JSON.stringify({ modules: { flies: {}, meic: {}, bwb: {} } }));
  return {
    port: 0,
    paths: {
      cherrypick: r,
      streamCacheDb: path.join(r, "nope.db"),
      watchdogLast: "",
      orchestratorConfig: path.join(r, "config.json"),
      consoleData: "",
      meicDir: path.join(r, "meic"),
      fliesDir: path.join(r, "flies"),
      earningsDir: "",
      calendarsDir: "",
      pmccDir: "",
      curveDir: "",
      bwbDir: path.join(r, "bwb"),
      gexDir: path.join(r, "gex"),
      reviewDir: "",
      manageDir: "",
      overviewDir: "",
      advisorDir: "",
      adviceDir: "",
      meicRiskConfig: "",
      fliesConfig: path.join(r, "config", "flies.json"),
      pmccConfigCandidates: [],
      calendarsConfigCandidates: [],
      curveConfigCandidates: [],
    },
  } as unknown as ConsoleConfig;
}

function writeJson(rel: string, doc: unknown): void {
  fs.writeFileSync(path.join(root, rel), JSON.stringify(doc));
}

function insert(db: Database.Database, table: string, row: Record<string, unknown>): void {
  const cols = Object.keys(row);
  db.prepare(`INSERT INTO ${table} (${cols.join(",")}) VALUES (${cols.map(() => "?").join(",")})`).run(...cols.map((c) => row[c]));
}

// --------------------------------------------------------------------------- flies
const FLIES_DDL = `
CREATE TABLE fly_positions (
  id INTEGER PRIMARY KEY AUTOINCREMENT, position_id TEXT UNIQUE, book_id TEXT, trade_date TEXT, arm TEXT,
  entry_mode TEXT, entry_window TEXT, symbol TEXT, kind TEXT, side TEXT, center REAL, wing_width REAL, far_width REAL,
  quantity INTEGER, net REAL, credit REAL, debit REAL, fees REAL, settlement_fees REAL, slippage_dollars REAL,
  expiry_payoff REAL, closed_before_expiry INTEGER, floor_dollars REAL, risk_free INTEGER, entry_time TEXT,
  completed_at TEXT, completion_latency_min REAL, pinned INTEGER, gross_pnl REAL, pnl REAL, status TEXT,
  entry_fill_status TEXT, completion_fill_status TEXT, void_reason TEXT, experiment_id TEXT);
CREATE TABLE fly_iterations (id INTEGER PRIMARY KEY, iteration_ts TEXT, trade_date TEXT, symbol TEXT, arm TEXT,
  center REAL, center_reason TEXT, underlying_price REAL);
CREATE TABLE fly_snapshots (id INTEGER PRIMARY KEY, iteration_ts TEXT, trade_date TEXT, symbol TEXT, status TEXT,
  quotes_fresh INTEGER, quotes_rejected INTEGER, underlying_price REAL);
CREATE TABLE fly_books (id INTEGER PRIMARY KEY, book_id TEXT, trade_date TEXT, arm TEXT, symbol TEXT, pnl REAL, status TEXT,
  settlement_price REAL, settlement_source TEXT);
CREATE TABLE fly_live_marks (id INTEGER PRIMARY KEY AUTOINCREMENT, iteration_ts TEXT, trade_date TEXT, position_id TEXT,
  kind TEXT, structure_mid REAL, mark_pnl REAL, spot REAL, open_margin REAL, resting_limit REAL);
`;

function fly(db: Database.Database, id: string, over: Record<string, unknown> = {}): void {
  insert(db, "fly_positions", {
    position_id: id, book_id: `${DAY}:control:SPX`, trade_date: DAY, arm: "control", entry_mode: "legged",
    symbol: "SPX", kind: "fly", side: "put", center: 6600, wing_width: 5, quantity: 1, net: 0.3, fees: 6.89,
    floor_dollars: 23.11, risk_free: 1, entry_time: `${DAY}T10:31:00-04:00`, status: "settled", entry_fill_status: "filled",
    ...over,
  });
}

function fliesLedger(): Database.Database {
  const db = new Database(path.join(root, "flies", "live_trades.db"));
  db.exec(FLIES_DDL);
  return db;
}

// --------------------------------------------------------------------------- meic
const MEIC_COLUMNS = [
  "id INTEGER PRIMARY KEY", "trade_date TEXT", "entry_time TEXT", "symbol TEXT", "arm TEXT", "status TEXT",
  "put_strike REAL", "call_strike REAL", "wing_width REAL", "quantity INTEGER", "net_credit REAL", "put_credit REAL",
  "call_credit REAL", "put_stop_cost REAL", "call_stop_cost REAL", "put_stop_fill_status TEXT", "call_stop_fill_status TEXT",
  "fill_confirmed_at TEXT", "pnl REAL", "fees REAL", "settlement_fees REAL", "exit_reason TEXT", "iv_rank_at_entry REAL",
  "dollar_multiplier REAL", "slippage_dollars REAL", "put_settle_value REAL", "call_settle_value REAL",
];

const IC = { symbol: "XSP", arm: "live", put_strike: 660, call_strike: 672, wing_width: 2, quantity: 1, dollar_multiplier: 100 };

function meicLedger(): Database.Database {
  const db = new Database(path.join(root, "meic", "meic_trades.db"));
  db.exec(`CREATE TABLE ic_trades (${MEIC_COLUMNS.join(", ")})`);
  return db;
}

// --------------------------------------------------------------------------- bwb
const BWB_DDL = `
  CREATE TABLE bwb_positions (id INTEGER PRIMARY KEY, position_id TEXT, symbol TEXT, arm TEXT,
    entry_session TEXT, closed_session TEXT, status TEXT, exit_reason TEXT, body_strike REAL,
    near_strike REAL, far_strike REAL, expiration TEXT, entry_spot REAL, entry_credit REAL,
    entry_max_loss REAL, peak_abs_delta REAL, below_flip_seen INTEGER, armed_at TEXT,
    addon_fired_at TEXT, addon_credit REAL, addon_short_strike REAL, addon_long_strike REAL,
    gross_pnl REAL, fees REAL, quantity INTEGER, itm_settlements INTEGER, entry_slippage REAL,
    addon_slippage REAL, settlement_fees REAL, fees_source TEXT, entry_fill_status TEXT);
  CREATE TABLE bwb_marks (id INTEGER PRIMARY KEY, position_id TEXT, session_date TEXT,
    close_cost REAL, spot REAL, usable INTEGER, refusal TEXT, marked_at REAL);
  CREATE TABLE bwb_trigger_ticks (id INTEGER PRIMARY KEY, session_date TEXT, measured INTEGER,
    spot_measured INTEGER, flip_measured INTEGER, refusal TEXT);
  CREATE TABLE bwb_entry_attempts (id INTEGER PRIMARY KEY, ts TEXT, trade_date TEXT, symbol TEXT,
    arm TEXT, outcome TEXT, credit REAL);
  CREATE TABLE bwb_management_events (id INTEGER PRIMARY KEY, position_id TEXT, occurred_at REAL,
    session_date TEXT, action TEXT, reason TEXT, executed INTEGER, gate TEXT);
  CREATE TABLE bwb_loop_iterations (id INTEGER PRIMARY KEY, ran_at REAL, session_date TEXT,
    phase TEXT, status TEXT);
  CREATE TABLE measurement_breaks (id INTEGER PRIMARY KEY, break_date TEXT, key TEXT, note TEXT);
`;

function bwbLedger(): Database.Database {
  const db = new Database(path.join(root, "bwb", "live_trades.db"));
  db.exec(BWB_DDL);
  return db;
}

beforeEach(() => {
  root = fs.mkdtempSync(path.join(os.tmpdir(), "console-live-today-"));
  config = cfg(root);
});

afterEach(() => {
  closePooledDbs();
});

describe("readLiveToday", () => {
  it("on a machine with no live ledgers: every loop absent, nothing needs a person, an empty total", () => {
    const p = readLiveToday(config, NOON);
    expect(p.session).toBe(DAY);
    expect(p.loops.map((l) => [l.loop, l.ledger])).toEqual([
      ["flies", "absent"],
      ["meic", "absent"],
      ["bwb", "absent"],
    ]);
    expect(p.attention).toEqual([]);
    expect(p.halted).toBe(false);
    expect(p.suite.armed).toEqual([]);
    // Nothing read is nothing summed -- the absent loops are named, not counted as zeros.
    expect(p.suite.summed).toEqual([]);
    expect(p.suite.excluded.map((e) => e.loop)).toEqual(["flies", "meic", "bwb"]);
    expect(p.suite.net).toBeNull();
  });

  it("composes each loop's day, in the money layout, and totals only the realised nets", () => {
    writeJson("config/flies.json", { live: { enabled: true, arm: "control", symbol: "SPX", daily_loss_halt_dollars: 500 } });
    writeJson("config/meic.json", { enable_live_trading: true, live: { symbol: "XSP", disarm_time: "16:30" } });
    writeJson("config/bwb.json", { symbol: "SPX", live: { enabled: false, arm: "bounce", daily_loss_halt_dollars: 100 } });
    writeJson("state/flies-live-arm.json", { date: DAY, at: `${DAY}T08:00:00-04:00` });
    writeJson("state/meic-live-arm.json", { date: DAY, at: `${DAY}T08:19:00-04:00` });
    writeJson("state/bwb-live-arm.json", { date: "2026-10-14", at: "2026-10-14T08:00:00-04:00" });

    const f = fliesLedger();
    // Settled today: $5 of the $9 fee total is settlement. gross 120 − fees 4 − settlement 5 = 111.
    fly(f, "f1", { gross_pnl: 120, fees: 9, settlement_fees: 5, pnl: 111, slippage_dollars: 2 });
    // Settled today, settlement split never recorded: settlement n/r, its fees stay whole.
    fly(f, "f2", { gross_pnl: -40, fees: 7, pnl: -47 });
    // Voided: out of every money total.
    fly(f, "f3", { gross_pnl: 500, fees: 7, pnl: 493, void_reason: "test row" });
    // Open short vertical: the floor after fees is the worst case.
    fly(f, "f4", { kind: "short_vertical", net: 1.5, fees: 3, status: "open", floor_dollars: null, risk_free: 0 });
    // A working entry: not a position, not an entry.
    fly(f, "f5", { status: "open", entry_fill_status: "pending" });
    insert(f, "fly_live_marks", { iteration_ts: `${DAY}T11:59:00-04:00`, trade_date: DAY, position_id: "f4", mark_pnl: -25, open_margin: 400 });
    f.close();

    const m = meicLedger();
    // Resolved today, settlement recorded: gross 25, fees 2.6, settlement 5, net 17.4.
    insert(m, "ic_trades", { ...IC, id: 1, trade_date: DAY, status: "expired", net_credit: 0.25, put_credit: 0.1, call_credit: 0.15,
      pnl: 25, fees: 7.6, settlement_fees: 5, exit_reason: "expired", put_settle_value: 0.4 });
    // Resolved yesterday: not today's.
    insert(m, "ic_trades", { ...IC, id: 2, trade_date: "2026-10-14", status: "stopped", net_credit: 0.35, put_credit: 0.14,
      call_credit: 0.21, pnl: -40, fees: 6.5, settlement_fees: 0, exit_reason: "stop_triggered" });
    // Open today, call side closing; and a working entry.
    insert(m, "ic_trades", { ...IC, id: 3, trade_date: DAY, status: "open", net_credit: 0.3, put_credit: 0.1, call_credit: 0.2,
      call_stop_fill_status: "pending" });
    insert(m, "ic_trades", { ...IC, id: 4, trade_date: DAY, status: "pending", net_credit: 0.31 });
    m.close();

    const b = bwbLedger();
    // Closed today (7-DTE, entered last week): gross 85 − fees 4.64 = 80.36, settlement recorded 0.
    insert(b, "bwb_positions", { position_id: "b1", symbol: "SPX", arm: "bounce", entry_session: "2026-10-08", closed_session: DAY,
      status: "closed", exit_reason: "expired", entry_credit: 0.85, gross_pnl: 85, fees: 4.64, settlement_fees: 0, quantity: 1,
      itm_settlements: 0, fees_source: "modeled" });
    // Open, entered today: $9.20 max loss per share is $920 worst case.
    insert(b, "bwb_positions", { position_id: "b2", symbol: "SPX", arm: "bounce", entry_session: DAY, status: "open",
      expiration: "2026-10-22", entry_credit: 0.8, entry_max_loss: 9.2, fees: 2.3, quantity: 1 });
    insert(b, "bwb_marks", { position_id: "b2", session_date: DAY, close_cost: -0.5, spot: 6600, usable: 1, marked_at: 1760540000 });
    b.close();

    const p = readLiveToday(config, NOON);
    const [flies, meic, bwb] = p.loops;

    expect(flies).toMatchObject({ loop: "flies", ledger: "ok", to: "/live/flies", open: 1, working: 1, entriesToday: 3 });
    expect(flies!.arm).toMatchObject({ armed: true, stale: false, pastDisarm: false, liveEnabled: true, arm: "control", symbol: "SPX" });
    expect(flies!.today).toMatchObject({ trades: 2, gross: 80, fees: 11, settlementFees: null, settlementRecorded: 5, settlementTrades: 1, net: 64 });
    // (1.5 − 5) × 100 − 3 − the assignment reserve: a loss, and at least the spread's own.
    expect(flies!.worstCase!).toBeLessThan(-353);
    expect(flies!.mark).toMatchObject({ value: -25, at: `${DAY}T11:59:00-04:00` });
    // The loop's breaker input is settled pnl, voided rows included -- its own query, mirrored.
    expect(flies!.breaker).toMatchObject({ limit: 500, figure: 557, tripped: false });

    expect(meic).toMatchObject({ loop: "meic", ledger: "ok", to: "/live/meic", open: 1, working: 2, entriesToday: 2, worstCase: null });
    expect(meic!.arm).toMatchObject({ armed: true, disarmTime: "16:30", pastDisarm: false, liveEnabled: true, symbol: "XSP" });
    expect(meic!.today).toMatchObject({ trades: 1, gross: 25, fees: 2.6, settlementFees: 5, net: 17.4 });
    expect(meic!.breaker).toMatchObject({ limit: null, tripped: null });

    expect(bwb).toMatchObject({ loop: "bwb", ledger: "ok", open: 1, working: 0, entriesToday: 1, worstCase: -920 });
    expect(bwb!.arm).toMatchObject({ armed: false, stale: true, liveEnabled: false, arm: "bounce", symbol: "SPX" });
    expect(bwb!.today).toMatchObject({ trades: 1, gross: 85, fees: 4.64, settlementFees: 0, net: 80.36 });
    // Unrealised net: (0.80 − 0.50) × 100 − fees to date 2.30.
    expect(bwb!.mark!.value).toBe(27.7);
    expect(bwb!.breaker).toMatchObject({ limit: 100, figure: 80.36, tripped: false });

    for (const l of p.loops) {
      const t = l.today!;
      expect(Math.round((t.gross - t.fees - t.settlementRecorded) * 100) / 100).toBe(t.net);
    }

    expect(p.attention).toEqual([]);
    expect(p.suite.armed).toEqual(["flies", "meic"]);
    expect(p.suite.summed).toEqual(["flies", "meic", "bwb"]);
    expect(p.suite.net).toBe(161.76);
    expect(p.suite.gross).toBe(190);
    expect(p.suite.fees).toBe(18.24);
    // flies' settlement is n/r, so the suite's is too -- never the recorded share dressed as the whole.
    expect(p.suite.settlementFees).toBeNull();
    expect(p.suite.trades).toBe(4);
  });

  it("raises every attention item from its own file or ledger state", () => {
    writeJson("config/flies.json", { live: { daily_loss_halt_dollars: 50 } });
    fs.writeFileSync(path.join(root, "state", "halt-live.flag"), "");
    const f = fliesLedger();
    fly(f, "loss", { gross_pnl: -60, fees: 6, pnl: -66 });
    // Left open from yesterday: settlement overdue.
    fly(f, "old", { trade_date: "2026-10-14", status: "open" });
    f.close();
    writeJson("flies/live_orphans.json", { at: `${DAY}T11:00:00-04:00`, orphans: [{ order_id: 7, status: "Live" }] });
    writeJson("flies/live_held.json", { unresolved: { "ext-1": {} }, unrecorded: {} });
    writeJson("flies/live_broker_health.json", { failing_since: `${DAY}T11:30:00-04:00`, failures: 4, last_error: "timeout" });

    const m = meicLedger();
    insert(m, "ic_trades", { ...IC, id: 1, trade_date: "2026-10-13", status: "pending", net_credit: 0.3 });
    m.close();
    // A bwb ledger that is not a database: a failed read, not an absent one.
    fs.writeFileSync(path.join(root, "bwb", "live_trades.db"), "not a database");

    const p = readLiveToday(config, NOON);
    expect(p.halted).toBe(true);
    expect(p.attention.map((a) => [a.loop, a.kind])).toEqual([
      ["flies", "orphaned_orders"],
      ["flies", "held_submission"],
      ["flies", "daily_loss_breaker"],
      ["flies", "overdue_settlement"],
      ["flies", "broker_failing"],
      ["meic", "overdue_settlement"],
      ["bwb", "ledger_unreadable"],
    ]);
    expect(p.loops[0]!.breaker).toMatchObject({ limit: 50, figure: -66, tripped: true });
    expect(p.loops[0]!.overdue).toEqual([{ session: "2026-10-14", positions: 1, pendingEntries: 0 }]);
    expect(p.loops[1]!.overdue).toEqual([{ session: "2026-10-13", positions: 0, pendingEntries: 1 }]);
    expect(p.loops[2]!.ledger).toBe("failed");
    // One loop unread makes the total n/r, not a total of the other two.
    expect(p.suite.net).toBeNull();
    expect(p.suite.excluded).toContainEqual({ loop: "bwb", reason: "the live ledger could not be read" });
  });

  it("marks a disabled module holding nothing as retired, and an open position brings it back", () => {
    writeJson("config.json", { modules: { flies: { enabled: false }, meic: { enabled: true } } });
    const quiet = readLiveToday(config, NOON);
    expect(quiet.loops.map((l) => [l.loop, l.moduleEnabled, l.retired])).toEqual([
      ["flies", false, true],
      ["meic", true, false],
      ["bwb", null, false], // a module the config does not name is not called off
    ]);
    const db = fliesLedger();
    fly(db, "F1", { status: "open", trade_date: DAY });
    db.close();
    closePooledDbs();
    const open = readLiveToday(config, NOON).loops[0]!;
    expect(open.open).toBeGreaterThan(0);
    expect(open.retired).toBe(false);
  });

  it("an arm record past the disarm time is not counted as armed", () => {
    writeJson("config/meic.json", { live: { disarm_time: "11:00" } });
    writeJson("state/meic-live-arm.json", { date: DAY });
    const p = readLiveToday(config, NOON);
    expect(p.loops[1]!.arm).toMatchObject({ armed: true, pastDisarm: true });
    expect(p.suite.armed).toEqual([]);
  });

  it("is served at GET /api/live/today", async () => {
    const app = Fastify();
    registerSecurity(app);
    registerLiveRoutes(app, config);
    await app.ready();
    const res = await app.inject({ method: "GET", url: "/api/live/today", headers: { host: "127.0.0.1:5070" } });
    expect(res.statusCode).toBe(200);
    expect(res.json().loops.map((l: { loop: string }) => l.loop)).toEqual(["flies", "meic", "bwb"]);
    await app.close();
  });
});
