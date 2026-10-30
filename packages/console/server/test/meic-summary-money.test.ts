import { describe, it, expect, beforeAll } from "vitest";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import Database from "better-sqlite3";
import type { ConsoleConfig } from "../src/config.js";
import { readMeicSummary } from "../src/readers/meic.js";

/**
 * The summary slide's money, pinned on a fixture ledger against the suite's money layout: stop
 * debits in whole-position dollars, settlement n/r rather than a partial sum, settled-only
 * populations, windows that follow the page's session, a by-arm card the arm select does not
 * narrow, gate counts per arm AND symbol, and a ledger with no attempts table still reading.
 */

let config: ConsoleConfig;
const DAY = "2026-10-14"; // a Wednesday; its week is 10-12..10-18
const ALL_ERAS = { symbol: null, profile: null, era: "ALL" };

const TRADES = `
  CREATE TABLE ic_trades (
    id INTEGER PRIMARY KEY, ic_order_id TEXT, trade_date TEXT, arm TEXT, symbol TEXT, status TEXT,
    pnl REAL, fees REAL, settlement_fees REAL, slippage_dollars REAL, net_credit REAL,
    dollar_multiplier REAL, quantity INTEGER, wing_width REAL, put_credit REAL, call_credit REAL,
    put_stop_cost REAL, call_stop_cost REAL, put_settle_value REAL, call_settle_value REAL,
    entry_time TEXT, call_delta_at_entry REAL, exit_reason TEXT
  );
  CREATE TABLE ic_spread_legs (id INTEGER PRIMARY KEY, ic_order_id TEXT, side TEXT, status TEXT);`;

function seed(db: Database.Database): void {
  const t = db.prepare(
    `INSERT INTO ic_trades (ic_order_id, trade_date, arm, symbol, status, pnl, fees, settlement_fees,
       net_credit, dollar_multiplier, quantity, wing_width, put_credit, call_credit, put_stop_cost,
       entry_time, call_delta_at_entry)
     VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1.0, 100, 1, 5, 0.5, 0.5, ?, ?, 0.12)`,
  );
  const leg = db.prepare("INSERT INTO ic_spread_legs (ic_order_id, side, status) VALUES (?, ?, ?)");
  // settled control SPX, put side stopped at an exit price of 1.50 a share
  t.run("t1", DAY, "control", "SPX", "stopped", 100, 10, 2, 1.5, `${DAY}T12:10:00`);
  leg.run("t1", "put", "stopped");
  leg.run("t1", "call", "expired");
  // an OPEN control SPX condor in the same hour: no money, no leg counts, no average dilution.
  // Its entry fees are on the row already, as the ledger writes them at the fill.
  t.run("t2", DAY, "control", "SPX", "open", null, 4, null, null, `${DAY}T12:20:00`);
  leg.run("t2", "put", "open");
  leg.run("t2", "call", "open");
  // settled live-shadow XSP
  t.run("t3", DAY, "live-shadow", "XSP", "expired", 50, 5, 1, null, `${DAY}T13:05:00`);
  leg.run("t3", "put", "expired");
  leg.run("t3", "call", "expired");
  // same week, settlement split never recorded
  t.run("t4", "2026-10-13", "control", "SPX", "expired", 20, 3, null, null, "2026-10-13T12:40:00");
  // a month earlier, outside the week
  t.run("t5", "2026-09-01", "control", "SPX", "expired", 40, 4, 1, null, "2026-09-01T12:40:00");
}

beforeAll(() => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "console-meicsummary-money-"));
  const dir = path.join(tmp, "meic");
  fs.mkdirSync(dir, { recursive: true });

  const paper = new Database(path.join(dir, "paper_trades.db"));
  paper.exec(TRADES);
  paper.exec(`CREATE TABLE entry_attempts (
    id INTEGER PRIMARY KEY, trade_date TEXT, arm TEXT, symbol TEXT, outcome TEXT, block_detail TEXT,
    gex_net REAL, iv_rank REAL, session_quality TEXT)`);
  seed(paper);
  const a = paper.prepare("INSERT INTO entry_attempts (trade_date, arm, symbol, outcome) VALUES (?, ?, ?, ?)");
  a.run(DAY, "control", "SPX", "filled");
  a.run(DAY, "control", "SPX", "gate_blocked");
  a.run(DAY, "control", "SPX", "gate_blocked");
  for (let i = 0; i < 5; i += 1) a.run(DAY, "control", "XSP", "gate_blocked");
  a.run(DAY, "live-shadow", "XSP", "filled");
  paper.close();

  // The live ledger has no entry_attempts table at all.
  const live = new Database(path.join(dir, "meic_trades.db"));
  live.exec(TRADES);
  seed(live);
  live.close();

  config = {
    port: 0,
    paths: {
      cherrypick: tmp,
      streamCacheDb: path.join(tmp, "stream_cache.db"),
      watchdogLast: path.join(tmp, "watchdog.last.json"),
      orchestratorConfig: path.join(tmp, "config.json"),
      consoleData: path.join(tmp, "console"),
      meicDir: dir,
      fliesDir: path.join(tmp, "flies"),
      earningsDir: path.join(tmp, "earnings"),
      gexDir: path.join(tmp, "gex"),
      reviewDir: path.join(tmp, "review"),
      manageDir: path.join(tmp, "manage"),
      overviewDir: path.join(tmp, "overview"),
      technicalsDir: path.join(tmp, "technicals"),
      advisorDir: path.join(tmp, "advisor"),
      adviceDir: path.join(tmp, "state", "advice"),
      meicRiskConfig: path.join(tmp, "config.risk.json"),
      fliesConfig: path.join(tmp, "config", "flies.json"),
    },
  };
});

describe("the MEIC summary's money", () => {
  it("prices the stop as a whole-position debit and counts only settled condors' legs", () => {
    const s = readMeicSummary(config, "paper", ALL_ERAS, "day", DAY, 1);
    const put = s.legs.find((l) => l.side === "put")!;
    expect(put.stopCost).toBe(150); // 1.50 a share x 100 x 1, not 1.5
    expect(put.stopped).toBe(1);
    expect(put.expired + put.stopped + put.settled + put.other).toBe(2); // t1, t3; the open t2 is out
  });

  it("states settlement when every row recorded it, and n/r with fees holding the total when one did not", () => {
    const day = readMeicSummary(config, "paper", ALL_ERAS, "day", DAY, 1);
    expect(day.hero.settlement).toBe(3);
    expect(day.hero.fees).toBe(12); // 15 total less the 3 recorded settlement
    expect(day.hero.net).toBe(135);
    const week = readMeicSummary(config, "paper", ALL_ERAS, "week", DAY, 1);
    expect(week.hero.settlement).toBeNull();
    expect(week.hero.fees).toBe(18); // the whole fee total, settlement unsplit inside it
    expect(week.hero.net).toBe(152);
  });

  it("widens week, month and year from the page's session, not from today", () => {
    expect(readMeicSummary(config, "paper", ALL_ERAS, "week", DAY, 1).window).toMatchObject({ from: "2026-10-12", to: "2026-10-18" });
    expect(readMeicSummary(config, "paper", ALL_ERAS, "month", DAY, 1).window).toMatchObject({ from: "2026-10-01", to: "2026-10-31" });
    expect(readMeicSummary(config, "paper", ALL_ERAS, "year", DAY, 1).window).toMatchObject({ from: "2026-01-01", to: "2026-12-31" });
    expect(readMeicSummary(config, "paper", ALL_ERAS, "week", "2026-09-01", 1).hero.trades).toBe(1); // t5's own week
  });

  it("averages an entry hour over its settled trades only", () => {
    const s = readMeicSummary(config, "paper", ALL_ERAS, "day", DAY, 1);
    const noon = s.entryTime.find((b) => b.hour === "12")!;
    expect(noon.count).toBe(2); // t1 settled, t2 open
    expect(noon.avg).toBe(90); // t1's 100 - 10, not (90 + 0) / 2
  });

  it("keeps every arm on the by-arm card when an arm is selected, with gate counts per arm and symbol", () => {
    const s = readMeicSummary(config, "paper", { ...ALL_ERAS, profile: "control" }, "day", DAY, 1);
    expect(s.hero.trades).toBe(1); // the hero DOES narrow to control
    const rows = s.arms.map((a) => `${a.arm}/${a.symbol}`).sort();
    expect(rows).toEqual(["control/SPX", "live-shadow/XSP"]);
    const control = s.arms.find((a) => a.arm === "control")!;
    expect(control.evaluated).toBe(3); // SPX only; its 5 XSP refusals belong to no row here
    expect(control.filled).toBe(1);
  });

  it("foots the by-arm card to the hero, leaving an open condor's entry fees out of both", () => {
    for (const range of ["day", "week", "month", "all"] as const) {
      const s = readMeicSummary(config, "paper", ALL_ERAS, range, DAY, 1);
      const arms = s.arms.reduce((sum, a) => sum + a.net, 0);
      expect(Math.round(arms * 100)).toBe(Math.round(s.hero.net * 100));
    }
    const control = readMeicSummary(config, "paper", ALL_ERAS, "day", DAY, 1).arms.find((a) => a.arm === "control")!;
    expect(control.net).toBe(90); // t1's 100 - 10; t2's 4 of entry fees wait for it to settle
  });

  it("reads a ledger with no entry_attempts table instead of blanking the slide", () => {
    const s = readMeicSummary(config, "live", ALL_ERAS, "day", DAY, 1);
    expect(s.hero.trades).toBe(2);
    expect(s.evaluations.evaluated).toBe(0);
    expect(s.arms.map((a) => a.arm).sort()).toEqual(["control", "live-shadow"]);
  });
});
