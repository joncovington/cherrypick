import { describe, it, expect, beforeEach, afterEach } from "vitest";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import Database from "better-sqlite3";
import Fastify from "fastify";
import type { ConsoleConfig } from "../src/config.js";
import { registerSecurity } from "../src/security.js";
import { registerLiveRoutes } from "../src/routes/live.js";
import { drawdownOf, periodBounds, readFliesLive, resolveLiveSession } from "../src/readers/fliesLive.js";
import { FETCHING, setBrokerCaller, settleBrokerRead, shape } from "../src/services/brokerBridge.js";
import { closePooledDbs } from "../src/readers/db.js";
import { resetMetricsCache, setMetricsCaller } from "../src/services/metricsBridge.js";

/**
 * The Live page's payload. What is pinned: the settled-net rule (core.ledgers `_flies_closed`:
 * `gross_pnl - fees` over `status = 'settled'` rows, by trade_date) against fixture rows; the mark
 * path summed per tick with its drawdown; "no live ledger" reported as absent rather than as an
 * empty day; and the broker bridge's field names against the SDK's underscored keys.
 */

let tmp: string;
let config: ConsoleConfig;

function cfg(root: string): ConsoleConfig {
  fs.mkdirSync(path.join(root, "flies"), { recursive: true });
  fs.mkdirSync(path.join(root, "state"), { recursive: true });
  fs.mkdirSync(path.join(root, "config"), { recursive: true });
  fs.writeFileSync(path.join(root, "config.json"), JSON.stringify({ modules: { flies: {} } }));
  fs.writeFileSync(
    path.join(root, "config", "flies.json"),
    JSON.stringify({ live: { enabled: false, arm: "control", symbol: "SPX", max_open_margin_dollars: 1000 } }),
  );
  return {
    port: 0,
    paths: {
      cherrypick: root,
      streamCacheDb: path.join(root, "nope.db"),
      watchdogLast: "",
      orchestratorConfig: path.join(root, "config.json"),
      consoleData: "",
      meicDir: "",
      fliesDir: path.join(root, "flies"),
      earningsDir: "",
      calendarsDir: "",
      pmccDir: "",
      curveDir: "",
      bwbDir: "",
      gexDir: path.join(root, "gex"),
      reviewDir: "",
      overviewDir: "",
      advisorDir: "",
      adviceDir: "",
      meicRiskConfig: "",
      fliesConfig: path.join(root, "config", "flies.json"),
      pmccConfigCandidates: [],
      calendarsConfigCandidates: [],
      curveConfigCandidates: [],
    },
  } as unknown as ConsoleConfig;
}

const DDL = `
CREATE TABLE fly_positions (
  id INTEGER PRIMARY KEY AUTOINCREMENT, position_id TEXT UNIQUE, book_id TEXT, trade_date TEXT, arm TEXT,
  entry_mode TEXT, symbol TEXT, kind TEXT, side TEXT, center REAL, wing_width REAL, quantity INTEGER, net REAL,
  credit REAL, debit REAL, fees REAL, floor_dollars REAL, risk_free INTEGER, entry_time TEXT, completed_at TEXT,
  gross_pnl REAL, pnl REAL, status TEXT, entry_fill_status TEXT, completion_fill_status TEXT, void_reason TEXT,
  far_width REAL, experiment_id TEXT);
CREATE TABLE fly_iterations (id INTEGER PRIMARY KEY, iteration_ts TEXT, trade_date TEXT, symbol TEXT, arm TEXT,
  center REAL, center_reason TEXT, underlying_price REAL);
CREATE TABLE fly_decisions (id INTEGER PRIMARY KEY, trade_date TEXT, arm TEXT, symbol TEXT, mode TEXT, reason TEXT,
  accepted INTEGER, first_seen TEXT, last_seen TEXT, occurrences INTEGER, center_first REAL, center_last REAL,
  position_id TEXT, detail TEXT);
CREATE TABLE fly_snapshots (id INTEGER PRIMARY KEY, iteration_ts TEXT, trade_date TEXT, symbol TEXT, status TEXT,
  quotes_fresh INTEGER, quotes_rejected INTEGER, underlying_price REAL);
CREATE TABLE fly_books (id INTEGER PRIMARY KEY, book_id TEXT, trade_date TEXT, arm TEXT, symbol TEXT, pnl REAL, status TEXT);
CREATE TABLE fly_live_marks (id INTEGER PRIMARY KEY AUTOINCREMENT, iteration_ts TEXT, trade_date TEXT, position_id TEXT,
  kind TEXT, structure_mid REAL, mark_pnl REAL, spot REAL, open_margin REAL, resting_limit REAL);
`;

function pos(db: Database.Database, id: string, day: string, over: Record<string, unknown> = {}) {
  const row = {
    position_id: id, book_id: `${day}:control:SPX`, trade_date: day, arm: "control", entry_mode: "legged",
    symbol: "SPX", kind: "fly", side: "put", center: 7500, wing_width: 5, quantity: 1, net: 0.3, fees: 6.89,
    floor_dollars: 23.11, risk_free: 1, entry_time: `${day}T10:31:00-04:00`, gross_pnl: 30, pnl: 23.11,
    status: "settled", ...over,
  };
  const cols = Object.keys(row);
  db.prepare(`INSERT INTO fly_positions (${cols.join(",")}) VALUES (${cols.map(() => "?").join(",")})`).run(...cols.map((c) => (row as Record<string, unknown>)[c]));
}

beforeEach(() => {
  tmp = fs.mkdtempSync(path.join(os.tmpdir(), "console-live-"));
  config = cfg(tmp);
  setBrokerCaller(async () => ({ ok: false, account: null, error: "no broker in tests" }));
  setMetricsCaller(() => ({ ok: true, metrics: { schema: "fly_book", n_records: 0, groups: {} }, error: null }));
});

afterEach(() => {
  closePooledDbs();
  setBrokerCaller();
  setMetricsCaller();
  resetMetricsCache();
});

describe("period bounds", () => {
  it("run Monday-to-session, first-of-month, and first-of-year", () => {
    const b = periodBounds("2026-09-17"); // a Thursday
    expect(b.today).toEqual(["2026-09-17", "2026-09-17"]);
    expect(b.week).toEqual(["2026-09-14", "2026-09-17"]);
    expect(b.month).toEqual(["2026-09-01", "2026-09-17"]);
    expect(b.year).toEqual(["2026-01-01", "2026-09-17"]);
    expect(periodBounds("2026-09-18").week[0]).toBe("2026-09-14"); // a Friday
    expect(periodBounds("2026-09-14").week).toEqual(["2026-09-14", "2026-09-14"]); // a Monday
  });

  it("follows the session across a month boundary, whatever the clock says", () => {
    // Shown to fail before 2026-09-22: the week came from the wall clock, so this read as the
    // current week and the payload test above went to zero the first Monday after its fixtures.
    expect(periodBounds("2026-09-02").week).toEqual(["2026-08-31", "2026-09-02"]);
    expect(periodBounds("2026-01-02").week).toEqual(["2025-12-29", "2026-01-02"]);
  });
});

describe("the live payload", () => {
  it("reports an absent live ledger as absent, not as an empty day", async () => {
    const first = readFliesLive(config, "2026-09-17");
    expect(first.ledger).toBe("absent");
    expect(first.periods.today).toEqual({
      net: 0, trades: 0, sessions: 0, paperNet: null,
      onRisk: { net: 0, peakRisk: 0, ratio: null, sessions: 0, of: 0, replayed: 0 },
    });
    expect(first.buyingPower.cap).toBe(1000);
    // the broker read never gates the page: a cold start answers "fetching", the next poll has it
    expect(first.buyingPower.account).toBeNull();
    expect(first.buyingPower.accountError).toBe(FETCHING);
    await settleBrokerRead();
    const out = readFliesLive(config, "2026-09-17");
    expect(out.buyingPower.accountError).toBe("no broker in tests");
    expect(out.arm.arm).toBe("control");
  });

  it("sums settled net per period by the core.ledgers flies rule, arm-scoped, and mirrors paper", async () => {
    const live = new Database(path.join(tmp, "flies", "live_trades.db"));
    live.exec(DDL);
    pos(live, "A", "2026-09-17", { gross_pnl: 30, fees: 6.89 }); // today: 23.11
    pos(live, "B", "2026-09-15", { gross_pnl: -300, fees: 13.44 }); // week: -313.44
    pos(live, "C", "2026-09-02", { gross_pnl: 100, fees: 10 }); // month: +90
    pos(live, "D", "2026-01-05", { gross_pnl: 50, fees: 5 }); // year: +45
    pos(live, "E", "2026-09-17", { status: "open", gross_pnl: null, pnl: null }); // not settled: excluded
    pos(live, "F", "2026-09-17", { arm: "gex", gross_pnl: 999, fees: 0 }); // another arm: excluded
    live.close();
    const paper = new Database(path.join(tmp, "flies", "paper_trades.db"));
    paper.exec(DDL);
    pos(paper, "P1", "2026-09-17", { gross_pnl: 110, fees: 10 }); // control: +100
    pos(paper, "P2", "2026-09-17", { arm: "callwall", gross_pnl: 500, fees: 0 });
    paper.close();

    const out = readFliesLive(config, "2026-09-17");
    expect(out.ledger).toBe("ok");
    expect(out.periods.today).toMatchObject({ net: 23.11, trades: 1, sessions: 1, paperNet: 100 });
    expect(out.periods.week.net).toBeCloseTo(23.11 - 313.44, 6);
    expect(out.periods.week.trades).toBe(2);
    expect(out.periods.month.net).toBeCloseTo(23.11 - 313.44 + 90, 6);
    expect(out.periods.year.net).toBeCloseTo(23.11 - 313.44 + 90 + 45, 6);
    expect(out.periods.year.sessions).toBe(4);
    expect(out.today.positions).toBe(3); // A, E, F for the day
    expect(out.today.open).toBe(1);
  });

  it("sums the mark path per tick, carries the latest mark and its drawdown, and lists gaps", async () => {
    const live = new Database(path.join(tmp, "flies", "live_trades.db"));
    live.exec(DDL);
    pos(live, "A", "2026-09-17", { status: "open", kind: "short_vertical", net: 1.0, gross_pnl: null, pnl: null, entry_fill_status: "filled" });
    pos(live, "B", "2026-09-17", { status: "open", gross_pnl: null, pnl: null });
    const mk = live.prepare("INSERT INTO fly_live_marks (iteration_ts, trade_date, position_id, kind, structure_mid, mark_pnl, spot, open_margin, resting_limit) VALUES (?,?,?,?,?,?,?,?,?)");
    mk.run("2026-09-17T10:31:00-04:00", "2026-09-17", "A", "short_vertical", -1.2, -23.44, 7500, 400, 0.9);
    mk.run("2026-09-17T10:31:00-04:00", "2026-09-17", "B", "fly", 0.9, 113.11, 7500, 400, null);
    mk.run("2026-09-17T10:32:00-04:00", "2026-09-17", "A", "short_vertical", -2.0, -103.44, 7490, 400, 0.9);
    mk.run("2026-09-17T10:32:00-04:00", "2026-09-17", "B", "fly", 0.5, 73.11, 7490, 400, null);
    live.prepare("INSERT INTO fly_snapshots (iteration_ts, trade_date, symbol, status) VALUES (?,?,?,?)").run(
      "2026-09-17T10:33:00-04:00", "2026-09-17", "SPX", "no_fresh_quotes",
    );
    live.close();

    const out = readFliesLive(config, "2026-09-17");
    expect(out.series.markPnl).toEqual([
      { m: 631, v: -23.44 + 113.11 },
      { m: 632, v: -103.44 + 73.11 },
    ]);
    expect(out.series.openMargin.map((p) => p.v)).toEqual([400, 400]);
    expect(out.today.markPnl).toBeCloseTo(-30.33, 6);
    expect(out.today.markedAt).toBe("2026-09-17T10:32:00-04:00");
    expect(out.today.peakMarkPnl).toBeCloseTo(89.67, 6);
    expect(out.today.maxDrawdown).toBeCloseTo(120, 6);
    expect(out.buyingPower.open).toBe(400);
    expect(out.series.gaps).toEqual([{ m: 633, reason: "no_fresh_quotes" }]);
    const a = out.positions.find((p) => p.positionId === "A")!;
    expect(a.mark).toEqual({ at: "2026-09-17T10:32:00-04:00", structureMid: -2.0, markPnl: -103.44, restingLimit: 0.9 });
    expect(a.entryFillStatus).toBe("filled");
  });
});

describe("return on session peak risk", () => {
  function marks(db: Database.Database, day: string, margins: number[]) {
    const mk = db.prepare("INSERT INTO fly_live_marks (iteration_ts, trade_date, position_id, open_margin) VALUES (?,?,?,?)");
    margins.forEach((m, i) => mk.run(`${day}T10:3${i}:00-04:00`, day, "X", m));
  }

  it("divides each finished session's settled net by its peak open margin, and matches the numerator", () => {
    const live = new Database(path.join(tmp, "flies", "live_trades.db"));
    live.exec(DDL);
    pos(live, "A1", "2026-09-15", { gross_pnl: 110, fees: 10 }); // +100
    pos(live, "A2", "2026-09-15", { gross_pnl: -10, fees: 10 }); // -20
    pos(live, "G1", "2026-09-15", { arm: "gex", gross_pnl: 999, fees: 0 }); // another arm: not in net
    marks(live, "2026-09-15", [200, 300, 250]); // peak 300, second tick
    pos(live, "B1", "2026-09-16", { gross_pnl: -140, fees: 10 }); // -150, no marks: not recorded
    pos(live, "C1", "2026-09-17", { gross_pnl: 60, fees: 10 }); // +50 settled...
    pos(live, "C2", "2026-09-17", { status: "open", gross_pnl: null, pnl: null }); // ...one still open
    marks(live, "2026-09-17", [100]);
    live.close();

    const out = readFliesLive(config, "2026-09-17");
    expect(out.performance.sessions.map((s) => [s.session, s.complete, s.peakRisk])).toEqual([
      ["2026-09-17", false, 100],
      ["2026-09-16", true, null],
      ["2026-09-15", true, 300],
    ]);
    const s15 = out.performance.sessions[2]!;
    expect(s15.net).toBeCloseTo(80, 6);
    expect(s15.onRisk).toBeCloseTo(80 / 300, 9);
    expect(s15.peakAt).toBe("2026-09-15T10:31:00-04:00");
    expect(out.performance.sessions[0]!.onRisk).toBeNull(); // open: the settled +50 is not the day's result
    expect(out.performance.sessions[1]!.onRisk).toBeNull(); // no peak recorded: never a zero denominator

    // Only 09-15 is both finished and recorded, so the -150 and the +50 stay out of the numerator
    // too -- counted in `of`, never in the ratio.
    const want = { net: 80, peakRisk: 300, sessions: 1, of: 3 };
    expect(out.performance.onRisk).toMatchObject(want);
    expect(out.performance.onRisk.ratio).toBeCloseTo(80 / 300, 9);
    expect(out.periods.week.onRisk).toMatchObject(want);
    expect(out.periods.today.onRisk).toEqual({ net: 0, peakRisk: 0, ratio: null, sessions: 0, of: 1, replayed: 0 });
  });
});

describe("which session the page shows", () => {
  function snapshot(file: string, ts: string) {
    const db = new Database(path.join(tmp, "flies", file));
    db.exec("CREATE TABLE IF NOT EXISTS fly_snapshots (id INTEGER PRIMARY KEY, iteration_ts TEXT, trade_date TEXT, status TEXT)");
    db.prepare("INSERT INTO fly_snapshots (iteration_ts, trade_date, status) VALUES (?,?,'ok')").run(ts, ts.slice(0, 10));
    db.close();
  }

  it("is the last settled session until today's opens, then today", () => {
    const live = new Database(path.join(tmp, "flies", "live_trades.db"));
    live.exec(DDL);
    pos(live, "A", "2026-09-16");
    pos(live, "B", "2026-09-17");
    live.close();
    // Overnight / pre-open: yesterday's numbers, not an empty day.
    expect(resolveLiveSession(config, null, "2026-09-18")).toEqual({ session: "2026-09-17", basis: "last_completed" });
    // A pre-open paper tick is not the open.
    snapshot("paper_trades.db", "2026-09-18T09:25:00-04:00");
    closePooledDbs();
    expect(resolveLiveSession(config, null, "2026-09-18").basis).toBe("last_completed");
    // The paper loop's 09:30 tick is.
    snapshot("paper_trades.db", "2026-09-18T09:30:00-04:00");
    closePooledDbs();
    expect(resolveLiveSession(config, null, "2026-09-18")).toEqual({ session: "2026-09-18", basis: "today" });
    // An asked-for session is always that session.
    expect(resolveLiveSession(config, "2026-09-16", "2026-09-18")).toEqual({ session: "2026-09-16", basis: "requested" });
  });

  it("is today once the pilot has a row for it, and today when nothing was ever settled", () => {
    const live = new Database(path.join(tmp, "flies", "live_trades.db"));
    live.exec(DDL);
    pos(live, "A", "2026-09-17");
    pos(live, "B", "2026-09-18", { status: "open", gross_pnl: null, pnl: null });
    live.close();
    expect(resolveLiveSession(config, null, "2026-09-18").basis).toBe("today");
    expect(resolveLiveSession(config, null, "2026-09-10")).toEqual({ session: "2026-09-10", basis: "today" });
  });
});

describe("the calibration reading", () => {
  it("is the performance slide's core.metrics read of the LIVE ledger, for the pilot's arm", () => {
    const asked: string[] = [];
    setMetricsCaller((dbPath, schema) => {
      asked.push(`${path.basename(dbPath)}|${schema}`);
      return {
        ok: true,
        metrics: {
          schema,
          n_records: 3,
          groups: {
            control: { reading: { sample: 2, expectancy: 11.5 }, session_nets: [], trade_nets: [] },
            gex: { reading: { sample: 1, expectancy: -4 }, session_nets: [], trade_nets: [] },
          },
        },
        error: null,
      };
    });
    const out = readFliesLive(config, "2026-09-17");
    expect(asked).toEqual(["live_trades.db|fly_book"]);
    expect(out.performance.calibration.reading).toEqual({ sample: 2, expectancy: 11.5 });
    expect(out.performance.calibration.error).toBeNull();

    setMetricsCaller(() => ({ ok: false, metrics: null, error: "calibration metrics unavailable" }));
    resetMetricsCache();
    const refused = readFliesLive(config, "2026-09-17").performance.calibration;
    expect(refused).toMatchObject({ reading: null, error: "calibration metrics unavailable" });
  });
});

describe("live vs paper", () => {
  it("measures the pilot's configured arm, not a hard-coded one", () => {
    const live = new Database(path.join(tmp, "flies", "live_trades.db"));
    live.exec(DDL);
    live.exec("ALTER TABLE fly_positions ADD COLUMN completion_latency_min REAL");
    pos(live, "C1", "2026-09-17", { credit: 2.4, completion_latency_min: 30 });
    pos(live, "C2", "2026-09-17", { kind: "short_vertical", credit: 2.2 });
    pos(live, "G1", "2026-08-03", { arm: "gex", credit: 1.3, completion_latency_min: 50 });
    live.close();
    const paper = new Database(path.join(tmp, "flies", "paper_trades.db"));
    paper.exec(DDL);
    paper.exec("ALTER TABLE fly_positions ADD COLUMN completion_latency_min REAL");
    pos(paper, "P1", "2026-09-17", { credit: 2.3, completion_latency_min: 20 });
    pos(paper, "P2", "2026-08-03", { credit: 2.3, completion_latency_min: 20 }); // not a live control session
    paper.close();

    // Shown to fail before 2026-09-30: the arm was the literal 'gex', so this read G1 alone.
    const lvp = readFliesLive(config, "2026-09-17").performance.liveVsPaper!;
    expect(lvp.arm).toBe("control");
    expect(lvp.live).toMatchObject({ sessions: 1, entries: 2, completed: 1, completionRatePct: 50 });
    expect(lvp.paper).toMatchObject({ sessions: 1, entries: 1, completed: 1, completionRatePct: 100 });
  });
});

describe("market series", () => {
  it("clips the recorder's pre-open samples to regular hours and baselines on the prior close", () => {
    fs.mkdirSync(path.join(tmp, "gex"), { recursive: true });
    const gex = new Database(path.join(tmp, "gex", "gex_history.db"));
    gex.exec("CREATE TABLE gex_spot_history (symbol TEXT, trade_date TEXT, ts REAL, spot REAL)");
    const ins = gex.prepare("INSERT INTO gex_spot_history VALUES (?,?,?,?)");
    // 2026-09-17 13:00Z = 09:00 ET (pre-open, frozen), 13:31Z = 09:31 ET, 14:00Z = 10:00 ET
    ins.run("SPX", "2026-09-17", Date.UTC(2026, 8, 17, 13, 0) / 1000, 7551.81);
    ins.run("SPX", "2026-09-17", Date.UTC(2026, 8, 17, 13, 31) / 1000, 7600);
    ins.run("SPX", "2026-09-17", Date.UTC(2026, 8, 17, 14, 0) / 1000, 7500);
    gex.close();
    const out = readFliesLive(config, "2026-09-17");
    expect(out.series.spxBaseline).toEqual({ value: 7551.81, source: "first_tick" }); // no stream cache here
    expect(out.series.spx.map((p) => p.m)).toEqual([571, 600]);
    expect(out.series.spx[0]!.v).toBeCloseTo(((7600 - 7551.81) / 7551.81) * 100, 6);
  });
});

describe("drawdown", () => {
  it("is peak-to-trough of the running path, never negative, null for no path", () => {
    expect(drawdownOf([])).toEqual({ maxDrawdown: null, peak: null });
    expect(drawdownOf([{ m: 1, v: 10 }, { m: 2, v: 30 }, { m: 3, v: -20 }, { m: 4, v: 5 }])).toEqual({ maxDrawdown: 50, peak: 30 });
    expect(drawdownOf([{ m: 1, v: -5 }, { m: 2, v: -1 }])).toEqual({ maxDrawdown: 0, peak: -1 });
  });
});

describe("the broker bridge", () => {
  it("reads the designated account's balances by the SDK's underscored names and the account marks", () => {
    const out = shape({
      ok: true,
      generated_at: "2026-09-17T21:00:00+00:00",
      accounts: [
        { account: "****1111", designated: false, balances: {}, value: 1, open_pl: 1, day_pl: 1, leg_count: 1, unpriced_count: 0 },
        {
          account: "****9991",
          designated: true,
          balances: { net_liquidating_value: "15507.576", derivative_buying_power: "3825.728", used_derivative_buying_power: "11684.796" },
          value: 6776, open_pl: -1135.5, day_pl: 241, leg_count: 39, unpriced_count: 0,
        },
      ],
    });
    expect(out.ok).toBe(true);
    expect(out.account).toEqual({
      account: "****9991",
      netLiquidatingValue: 15507.576,
      derivativeBuyingPower: 3825.728,
      usedDerivativeBuyingPower: 11684.796,
      value: 6776,
      openPl: -1135.5,
      dayPl: 241,
      legCount: 39,
      unpricedCount: 0,
      at: "2026-09-17T21:00:00+00:00",
    });
    expect(shape({ ok: false, error: "credentials absent" })).toEqual({ ok: false, account: null, error: "credentials absent" });
  });
});

describe("GET /api/live/flies", () => {
  it("serves the payload and refuses a malformed session", async () => {
    const app = Fastify();
    registerSecurity(app);
    registerLiveRoutes(app, config);
    await app.ready();
    const ok = await app.inject({ method: "GET", url: "/api/live/flies?session=2026-09-17", headers: { host: "127.0.0.1:5070" } });
    expect(ok.statusCode).toBe(200);
    expect(ok.json().session).toBe("2026-09-17");
    const bad = await app.inject({ method: "GET", url: "/api/live/flies?session=yesterday", headers: { host: "127.0.0.1:5070" } });
    expect(bad.statusCode).toBe(400);
    await app.close();
  });
});
