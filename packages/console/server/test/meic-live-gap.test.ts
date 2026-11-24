import { describe, it, expect, beforeEach } from "vitest";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import Database from "better-sqlite3";
import type { ConsoleConfig } from "../src/config.js";
import type { MeicLiveGapSide } from "@console/shared";
import { meicTwins, mirrorRows, readMeicLiveGap } from "../src/readers/meicLiveGap.js";

/**
 * MEIC live against paper. The guards: a live condor pairs with
 * the paper twin on the same strikes before entry order; paper sessions live sat out are counted on
 * their own line; an open condor has no net and no gap; the paired totals are the sums of their
 * rows; other paper arms and paper sessions before live began are not read; a missing ledger is
 * "absent", not a thrown route.
 */

let tmp: string;
let config: ConsoleConfig;

const SCHEMA = `CREATE TABLE ic_trades (
  id INTEGER PRIMARY KEY, trade_date TEXT, entry_time TEXT, symbol TEXT, put_strike REAL, call_strike REAL,
  wing_width REAL, net_credit REAL, quantity INTEGER, status TEXT, pnl REAL, fees REAL, arm TEXT)`;

type Row = [string, string, number, number, number, string, number | null, number | null, string];

function ledger(file: string, rows: Row[]): void {
  const db = new Database(path.join(tmp, file));
  db.exec(SCHEMA);
  const ins = db.prepare(
    `INSERT INTO ic_trades (trade_date, entry_time, symbol, put_strike, call_strike, wing_width, net_credit, quantity,
       status, pnl, fees, arm) VALUES (?, ?, 'SPX', ?, ?, 10, ?, 1, ?, ?, ?, ?)`,
  );
  for (const r of rows) ins.run(...r);
  db.close();
}

beforeEach(() => {
  tmp = fs.mkdtempSync(path.join(os.tmpdir(), "meic-live-gap-"));
  config = { paths: { meicDir: tmp } } as unknown as ConsoleConfig;
  ledger("meic_trades.db", [
    // 10-20: two live condors; the second entered first's strikes on paper first
    ["2026-10-20", "2026-10-20 14:00", 7600, 7650, 1.6, "expired", 160, 7, "live"],
    ["2026-10-20", "2026-10-20 14:10", 7610, 7660, 1.5, "stopped", -200, 8, "live"],
    // 10-22: open
    ["2026-10-22", "2026-10-22 14:00", 7700, 7750, 2.0, "open", null, 6, "live"],
    ["2026-10-22", "2026-10-22 14:05", 7700, 7750, 2.0, "cancelled", null, null, "live"],
  ]);
  ledger("paper_trades.db", [
    ["2026-10-19", "2026-10-19 14:00", 7500, 7550, 1.7, "expired", 170, 5, "live-shadow"], // before live began
    ["2026-10-20", "2026-10-20 13:58", 7610, 7660, 1.55, "stopped", -190, 5, "live-shadow"],
    ["2026-10-20", "2026-10-20 14:01", 7600, 7650, 1.62, "expired", 162, 5, "live-shadow"],
    ["2026-10-20", "2026-10-20 14:00", 7600, 7650, 1.62, "expired", 999, 5, "control"],
    ["2026-10-21", "2026-10-21 14:00", 7650, 7700, 1.8, "expired", 180, 5, "live-shadow"], // live sat out
    ["2026-10-22", "2026-10-22 14:00", 7700, 7750, 2.01, "open", null, 5, "live-shadow"],
  ]);
});

describe("readMeicLiveGap", () => {
  it("pairs the same strikes first, counts what live sat out, and leaves open condors without a gap", () => {
    const g = readMeicLiveGap(config);
    expect(g.ledger).toBe("ok");
    expect(g.since).toBe("2026-10-20");
    expect(g.rows.map((r) => r.session)).toEqual(["2026-10-22", "2026-10-21", "2026-10-20", "2026-10-20"]);
    const [open, satOut, second, first] = g.rows;
    expect(open!.live?.status).toBe("open");
    expect(open!.gap).toBeNull();
    expect(satOut!.live).toBeNull();
    expect(satOut!.paper?.net).toBe(175);
    // 7600/7650: live 160 - 7 against paper 162 - 5
    expect(first!.live?.putStrike).toBe(7600);
    expect(first!.paper?.putStrike).toBe(7600);
    expect(first!.gap).toBe(-4);
    // 7610/7660: live -200 - 8 against paper -190 - 5
    expect(second!.paper?.putStrike).toBe(7610);
    expect(second!.gap).toBe(-13);
  });

  it("totals the paired rows to the cent and keeps the sat-out sessions apart", () => {
    const t = readMeicLiveGap(config).totals;
    expect(t).toEqual({
      paired: 2,
      liveNet: -55, // 153 + (-208)
      paperNet: -38, // 157 + (-195)
      gap: -17,
      paperOnly: 1,
      paperOnlyNet: 175,
      liveOnly: 0,
    });
  });

  it("is absent, not a thrown route, without a live ledger", () => {
    fs.rmSync(path.join(tmp, "meic_trades.db"));
    expect(readMeicLiveGap(config)).toMatchObject({ ledger: "absent", rows: [], since: null });
  });

  it("keeps live-shadow as the mirror and lists nothing until it retires", () => {
    const g = readMeicLiveGap(config);
    expect(g.twin).toBe("live-shadow");
    expect(g.unmirrored).toEqual([]);
  });
});

/** The mirror follows the arm live ran: `live-shadow` until it retires, then each session's twin. */
describe("the mirror per arm", () => {
  const side = (entry: string): MeicLiveGapSide => ({ entry, symbol: "SPX", putStrike: 7600, callStrike: 7650, width: 10, credit: 1.6, status: "expired", gross: 10, fees: 1, net: 9 });
  const twins = new Map([["pm-200", "live-shadow-pm-200"], ["pm-1400", "live-shadow-pm-1400"]]);
  const day = (arms: Record<string, string[]>) => new Map(Object.entries(arms).map(([a, e]) => [a, e.map(side)]));

  it("uses live-shadow for every session before it retired, whatever twins exist", () => {
    const byArm = new Map([["2026-11-20", day({ "live-shadow": ["a"], "live-shadow-pm-200": ["b"] })]]);
    const out = mirrorRows(byArm, new Map([["2026-11-20", new Set(["pm-200"])]]), twins, null);
    expect(out.paper.get("2026-11-20")?.map((s) => s.entry)).toEqual(["a"]);
    expect(out.unmirrored).toEqual([]);
  });

  it("from the retirement uses the twin of the arm live ran that session, and no other", () => {
    const byArm = new Map([
      ["2026-11-23", day({ "live-shadow": ["old"] })],
      ["2026-11-24", day({ "live-shadow-pm-200": ["a"], "live-shadow-pm-1400": ["b"] })],
      ["2026-11-25", day({ "live-shadow-pm-200": ["c"], "live-shadow-pm-1400": ["d"] })],
    ]);
    const liveArms = new Map([["2026-11-24", new Set(["pm-200"])], ["2026-11-25", new Set(["pm-1400"])]]);
    const out = mirrorRows(byArm, liveArms, twins, "2026-11-24");
    expect(out.paper.get("2026-11-23")?.map((s) => s.entry)).toEqual(["old"]);
    expect(out.paper.get("2026-11-24")?.map((s) => s.entry)).toEqual(["a"]);
    expect(out.paper.get("2026-11-25")?.map((s) => s.entry)).toEqual(["d"]);
  });

  it("gives a session whose arm has no twin no mirror, and says so, never borrowing another arm's twin", () => {
    const byArm = new Map([["2026-11-24", day({ "live-shadow-pm-200": ["a"], "live-shadow": ["x"] })]]);
    const out = mirrorRows(byArm, new Map([["2026-11-24", new Set(["control"])]]), twins, "2026-11-24");
    expect(out.paper.has("2026-11-24")).toBe(false);
    expect(out.unmirrored).toEqual(["2026-11-24"]);
  });

  it("reads it end to end: registry twins, the live ledger's attempts, the paper ledger's break", () => {
    for (const f of ["meic_trades.db", "paper_trades.db"]) fs.rmSync(path.join(tmp, f));
    ledger("meic_trades.db", [
      ["2026-11-24", "2026-11-24 14:00", 7600, 7650, 1.6, "expired", 160, 7, "live"],
      ["2026-11-25", "2026-11-25 14:00", 7600, 7650, 1.6, "expired", 160, 7, "live"],
    ]);
    ledger("paper_trades.db", [
      ["2026-11-24", "2026-11-24 14:01", 7600, 7650, 1.62, "expired", 162, 5, "live-shadow-pm-200"],
      ["2026-11-24", "2026-11-24 14:02", 7600, 7650, 1.7, "expired", 999, 5, "live-shadow-pm-1400"],
      ["2026-11-25", "2026-11-25 14:01", 7600, 7650, 1.62, "expired", 162, 5, "live-shadow-pm-200"],
    ]);
    const live = new Database(path.join(tmp, "meic_trades.db"));
    live.exec("CREATE TABLE entry_attempts (id INTEGER PRIMARY KEY, trade_date TEXT, arm TEXT, outcome TEXT)");
    live.exec("INSERT INTO entry_attempts (trade_date, arm, outcome) VALUES ('2026-11-24', 'pm-200', 'filled'), ('2026-11-25', 'control', 'filled')");
    live.close();
    const paper = new Database(path.join(tmp, "paper_trades.db"));
    paper.exec("CREATE TABLE measurement_breaks (id INTEGER PRIMARY KEY, break_date TEXT, scope TEXT, kind TEXT)");
    paper.exec("INSERT INTO measurement_breaks (break_date, scope, kind) VALUES ('2026-11-24', 'live-shadow', 'arm_retired')");
    paper.close();
    const registry = path.join(tmp, "meic.risk.json");
    fs.writeFileSync(
      registry,
      JSON.stringify({ profiles: { "live-shadow-pm-200": { enabled: true, shadow_of: "pm-200" }, "live-shadow-pm-1400": { enabled: true, shadow_of: "pm-1400" } } }),
    );
    const g = readMeicLiveGap({ paths: { meicDir: tmp, meicRiskConfig: registry } } as unknown as ConsoleConfig);
    expect(g.twin).toBe("live-shadow before 2026-11-24, then each session's arm twin");
    expect(g.unmirrored).toEqual(["2026-11-25"]);
    // 11-24: live 160 − 7 against pm-200's twin 162 − 5, not pm-1400's 999; 11-25: no twin for control, so live alone.
    const [d25, d24] = g.rows;
    expect(d24!.paper?.net).toBe(157);
    expect(d24!.gap).toBe(-4);
    expect(d25!.paper).toBeNull();
    expect(g.totals.liveOnly).toBe(1);
  });

  it("reads no twins from an absent or unreadable registry", () => {
    expect(meicTwins({ paths: { meicRiskConfig: path.join(tmp, "nope.json") } } as unknown as ConsoleConfig).size).toBe(0);
    fs.writeFileSync(path.join(tmp, "bad.json"), "{not json");
    expect(meicTwins({ paths: { meicRiskConfig: path.join(tmp, "bad.json") } } as unknown as ConsoleConfig).size).toBe(0);
    expect(meicTwins({ paths: {} } as unknown as ConsoleConfig).size).toBe(0);
  });

  it("takes only plain twins from the registry: an override makes it an experiment beside the arm", () => {
    const registry = path.join(tmp, "r.json");
    fs.writeFileSync(
      registry,
      JSON.stringify({
        profiles: {
          "t-plain": { enabled: false, shadow_of: "a", _note: "retired" },
          "t-plus": { enabled: true, shadow_of: "b", min_iv_rank: 0 },
          "live-shadow": { enabled: true, follows_live: true },
        },
      }),
    );
    expect([...meicTwins({ paths: { meicRiskConfig: registry } } as unknown as ConsoleConfig)]).toEqual([["a", "t-plain"]]);
  });
});
