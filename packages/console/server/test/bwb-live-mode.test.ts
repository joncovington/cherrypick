import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import Database from "better-sqlite3";
import Fastify, { type FastifyInstance } from "fastify";
import type { ConsoleConfig } from "../src/config.js";
import { registerSecurity } from "../src/security.js";
import { registerModuleRoutes } from "../src/routes/modules.js";
import { readBwb, readBwbHistory, readBwbMeta } from "../src/readers/bwb.js";
import { readDecisions } from "../src/readers/decisions.js";
import { ledgerFile, readModulePerformance } from "../src/readers/performance.js";
import { resetMetricsCache, setMetricsCaller } from "../src/services/metricsBridge.js";
import { closePooledDbs } from "../src/readers/db.js";

/**
 * bwb has had a narrow live pilot since 2026-09-18 (`live_trades.db`, the paper schema in its own
 * file), but until 2026-10-12 the console read it as paper-only: every route ignored `?mode=` and
 * the page hard-coded a paper badge. These pin the two books apart, and the live book's empty
 * state: a ledger with no positions reads as nothing (no rows, null totals), never a book of zeros.
 *
 * The fixture's paper book holds one completed position and a decision; the live book holds only
 * the schema, which is the real live ledger's state today.
 */

const SCHEMA = `
  CREATE TABLE bwb_positions (id INTEGER PRIMARY KEY, position_id TEXT, symbol TEXT, arm TEXT,
    entry_session TEXT, closed_session TEXT, status TEXT, exit_reason TEXT, body_strike REAL,
    near_strike REAL, far_strike REAL, expiration TEXT, entry_spot REAL, entry_credit REAL,
    entry_max_loss REAL, peak_abs_delta REAL, below_flip_seen INTEGER, armed_at TEXT,
    addon_fired_at TEXT, addon_credit REAL, gross_pnl REAL, fees REAL, quantity INTEGER,
    itm_settlements INTEGER, entry_slippage REAL, addon_slippage REAL);
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
  CREATE TABLE bwb_decisions (id INTEGER PRIMARY KEY, trade_date TEXT, arm TEXT, symbol TEXT,
    mode TEXT, reason TEXT, accepted INTEGER, occurrences INTEGER, detail TEXT);
`;

let dir: string;
let config: ConsoleConfig;
let app: FastifyInstance;

beforeAll(async () => {
  dir = fs.mkdtempSync(path.join(os.tmpdir(), "console-bwb-live-"));
  const paper = new Database(path.join(dir, "paper_trades.db"));
  paper.exec(SCHEMA);
  paper.exec(`
    INSERT INTO bwb_positions (position_id, symbol, arm, entry_session, closed_session, status,
      exit_reason, entry_credit, gross_pnl, fees, quantity, itm_settlements, entry_slippage)
    VALUES ('p1', 'SPX', 'control', '2026-10-01', '2026-10-08', 'closed', 'expired', 0.85, 85, 4.64, 1, 0, 1.2);
    INSERT INTO bwb_loop_iterations (ran_at, session_date, phase, status) VALUES (1760000000, '2026-10-08', 'entry', 'ok');
    INSERT INTO bwb_decisions (trade_date, arm, symbol, reason, accepted, occurrences)
      VALUES ('2026-10-08', 'control', 'SPX', 'entered', 1, 1);
  `);
  paper.close();
  const live = new Database(path.join(dir, "live_trades.db"));
  live.exec(SCHEMA);
  live.close();
  config = {
    port: 0,
    paths: { bwbDir: dir, orchestratorConfig: path.join(dir, "absent-config.json") },
  } as unknown as ConsoleConfig;

  app = Fastify();
  registerSecurity(app);
  registerModuleRoutes(app, config);
  await app.ready();
});

afterAll(async () => {
  await app.close();
  closePooledDbs();
});

afterEach(() => {
  setMetricsCaller();
  resetMetricsCache();
});

const get = (url: string) => app.inject({ method: "GET", url, headers: { host: "127.0.0.1:5070" } });

describe("bwb's readers in live mode", () => {
  it("read the live book as present and empty -- no rows, no zeros dressed as a result", () => {
    const live = readBwb(config, "live");
    expect(live.mode).toBe("live");
    expect(live.dbPresent).toBe(true);
    expect(live.openPositions).toEqual([]);
    expect(live.arms).toEqual([]);
    expect(live.fireCounts).toEqual([]);
    expect(live.session).toBeNull();

    const history = readBwbHistory(config, { arm: null, symbol: null }, undefined, "live");
    expect(history.mode).toBe("live");
    expect(history.rows).toEqual([]);
    expect(history.total).toBe(0);
    // Not { positions: 0, net: 0, ... }: an empty book has no net.
    expect(history.totals).toBeNull();

    expect(readBwbMeta(config, "live")).toEqual({ mode: "live", arms: [], symbols: [], sessions: [] });
  });

  it("never fall back to the paper book", () => {
    expect(readBwb(config).mode).toBe("paper");
    expect(readBwb(config).arms).toHaveLength(1);
    const paper = readBwbHistory(config, { arm: null, symbol: null });
    expect(paper.mode).toBe("paper");
    expect(paper.totals?.positions).toBe(1);
    expect(readBwbMeta(config).arms).toEqual(["control"]);
  });

  it("read the live decision journal from the live file, and refuse live for a module with none", () => {
    expect(readDecisions(config, "bwb", null).rows.map((r) => r.arm)).toEqual(["control"]);
    const live = readDecisions(config, "bwb", null, "live");
    expect(live).toEqual({ module: "bwb", mode: "live", tradeDate: null, rows: [] });
    const curve = readDecisions(config, "curve", "2026-10-08", "live");
    expect(curve.rows).toEqual([]);
    expect(curve.mode).toBe("live");
  });
});

describe("bwb's routes", () => {
  it("accept ?mode=live and answer from the live book", async () => {
    const body = (await get("/api/bwb?mode=live")).json();
    expect(body.mode).toBe("live");
    expect(body.arms).toEqual([]);
    const history = (await get("/api/bwb/history?mode=live")).json();
    expect(history.mode).toBe("live");
    expect(history.rows).toEqual([]);
    expect(history.totals).toBeNull();
    expect((await get("/api/bwb/meta?mode=live")).json().mode).toBe("live");
    expect((await get("/api/bwb/decisions?mode=live")).json().rows).toEqual([]);
  });

  it("default to paper", async () => {
    expect((await get("/api/bwb")).json().mode).toBe("paper");
    expect((await get("/api/bwb/history")).json().totals.positions).toBe(1);
    expect((await get("/api/bwb/decisions")).json().rows[0].arm).toBe("control");
  });
});

describe("bwb's performance slide in live mode", () => {
  it("reads live_trades.db rather than refusing or reading paper", () => {
    expect(ledgerFile("bwb", "live")).toBe("live_trades.db");
    const seen: string[] = [];
    setMetricsCaller((db) => {
      seen.push(path.basename(db));
      return { ok: true, error: null, metrics: { schema: "bwb_132", n_records: 0, groups: {} } };
    });
    const out = readModulePerformance(config, "bwb", "ALL", "live");
    expect(seen).toEqual(["live_trades.db"]);
    expect(out.ok).toBe(true);
    expect(out.mode).toBe("live");
    expect(out.groups).toEqual([]);
    expect(out.pairs).toEqual([]);
  });
});
