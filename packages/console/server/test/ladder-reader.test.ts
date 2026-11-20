import { afterAll, beforeAll, describe, expect, it } from "vitest";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import Database from "better-sqlite3";
import { LADDER_HOSPITAL_DAYS } from "@console/shared";
import type { ConsoleConfig } from "../src/config.js";
import { closePooledDbs } from "../src/readers/db.js";
import { daysBetween, etDate, readLadder, readLadderHistory } from "../src/readers/ladder.js";
import { readDecisions } from "../src/readers/decisions.js";

/**
 * The ladder's lots against a ledger built from the module's schema (`fixtures/ladder-ledger-schema.sql`,
 * generated from its `db.connect`), filled with invented lots. A lot is bought at a limit and sold at its
 * target: entry is the purchase (-), exit the sale (+), and they add up to gross; gross less the three
 * cost columns is net. The module owns every figure, so the reader only places them.
 */

const SCHEMA = fs.readFileSync(path.join(__dirname, "fixtures", "ladder-ledger-schema.sql"), "utf-8");
const NOW = new Date("2026-11-20T15:00:00Z");
let config: ConsoleConfig;

const COLS = `position_id, arm, symbol, entry_rule, target_rule, quantity, ref_close, buy_level, buy_price, buy_session, buy_time,
  status, target_price, target_stage, last_bid, last_mark_time, sell_price, sell_session, sell_time, exit_reason, hold_days,
  entry_value, exit_value, gross_pnl, fees, settlement, slippage, net_pnl, net_pct`;

beforeAll(() => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "console-ladder-"));
  const dir = path.join(tmp, "ladder");
  fs.mkdirSync(dir);
  const db = new Database(path.join(dir, "paper_trades.db"));
  db.exec(SCHEMA);
  const ins = db.prepare(`INSERT INTO ladder_positions (${COLS}) VALUES (${COLS.split(",").map(() => "?").join(",")})`);
  // Sold in two days: 20 x 40.00 = -800 in, 20 x 41.60 = 832 out, gross 32, fees 0.05, net 31.95.
  ins.run("session:TQQQ:2026-11-12:1", "session", "TQQQ", "session", "fixed", 20, 43, 40, 40, "2026-11-12", "2026-11-12T10:00:00-05:00",
    "closed", 41.6, "gain", 40.5, "2026-11-13T11:00:00-05:00", 41.6, "2026-11-14", "2026-11-14T09:30:00-05:00", "target_gain", 2,
    -800, 832, 32, 0.05, 0, 0, 31.95, 3.99);
  // A second sale a week later, newer.
  ins.run("chain:SOXL:2026-11-16:1", "chain", "SOXL", "chain", "fixed", 10, 50, 46.5, 46.5, "2026-11-16", "2026-11-16T09:45:00-05:00",
    "closed", 48.36, "gain", 47, "2026-11-18T10:00:00-05:00", 48.4, "2026-11-18", "2026-11-18T10:30:00-05:00", "target_gain", 2,
    -465, 484, 19, 0.04, 0, 0, 18.96, 4.08);
  // Open, bought yesterday, marked.
  ins.run("once:TQQQ:2026-11-19:1", "once", "TQQQ", "once", "fixed", 20, 50, 46.5, 46.5, "2026-11-19", "2026-11-19T16:01:31-05:00",
    "open", 48.36, "gain", 46.1, "2026-11-19T19:59:46-05:00", null, null, null, null, null, -930, null, null, null, null, null, null, null);
  // Open past the group's 18 days, never marked.
  ins.run("decay:SOXL:2026-10-20:1", "decay", "SOXL", "once", "decay", 10, 60, 55.8, 55.8, "2026-10-20", "2026-10-20T10:00:00-04:00",
    "open", 55.8, "breakeven", null, null, null, null, null, null, null, -558, null, null, null, null, null, null, null);
  db.prepare("INSERT INTO ladder_decisions (trade_date, arm, symbol, mode, reason, accepted, occurrences) VALUES (?,?,?,?,?,?,?)").run(
    "2026-11-20", "chain", "SOXL", "paper", "above_level", 0, 12,
  );
  db.prepare("INSERT INTO ladder_loop_iterations (ran_at, session_date, phase, status) VALUES (?,?,?,?)").run(
    NOW.getTime() / 1000 - 90, "2026-11-20", "session", "ok",
  );
  db.prepare("INSERT INTO measurement_breaks (break_date, key, old_value, new_value, note) VALUES (?,?,?,?,?)").run(
    "2026-11-13", "arms", "3", "4", "session arm added",
  );
  db.close();
  config = { paths: { ladderDir: dir } } as unknown as ConsoleConfig;
});

afterAll(() => closePooledDbs());

const history = (from: string | null = null, to: string | null = null, arm: string | null = null) =>
  readLadderHistory(config, { arm, range: { from, to } });

describe("the ladder's sold lots", () => {
  it("list only sold lots, newest sale first", () => {
    expect(history().rows.map((r) => r.positionId)).toEqual(["chain:SOXL:2026-11-16:1", "session:TQQQ:2026-11-12:1"]);
  });

  it("sign the purchase negative and the sale positive, and add up", () => {
    const r = history().rows.find((x) => x.positionId === "session:TQQQ:2026-11-12:1")!;
    expect([r.quantity, r.price, r.entryCash, r.exitCash]).toEqual([20, -40, -800, 832]);
    expect(r.entryCash! + r.exitCash!).toBeCloseTo(r.grossPnl!, 2);
    expect(r.grossPnl! - r.fees! - r.settlementFees! - r.slippage!).toBeCloseTo(r.netPnl!, 2);
    expect(r.exitKind).toBe("closed");
    expect(r.netPct).toBe(3.99);
    expect(r.holdDays).toBe(2);
  });

  it("total every matching lot through the same arithmetic", () => {
    const t = history().totals;
    expect(t).toEqual({ positions: 2, gross: 51, fees: 0.09, settlementFees: 0, slippage: 0, net: 50.91 });
    expect(t.gross - t.fees - t.settlementFees - t.slippage).toBeCloseTo(t.net, 2);
  });

  it("bound the sale, not the purchase, and filter by arm", () => {
    expect(history("2026-11-15").rows.map((r) => r.arm)).toEqual(["chain"]);
    expect(history(null, "2026-11-14").rows.map((r) => r.arm)).toEqual(["session"]);
    expect(history(null, null, "once").rows).toEqual([]);
  });
});

describe("the ladder's open lots", () => {
  const read = () => readLadder(config, NOW);

  it("value a marked lot at its bid and leave an unmarked one unvalued", () => {
    const lots = read().openLots;
    const marked = lots.find((l) => l.arm === "once")!;
    expect([marked.lastBid, marked.valueAtBid, marked.unrealisedAtBid]).toEqual([46.1, 922, -8]);
    const bare = lots.find((l) => l.arm === "decay")!;
    expect([bare.lastBid, bare.valueAtBid, bare.unrealisedAtBid]).toEqual([null, null, null]);
    expect(bare.targetStage).toBe("breakeven");
  });

  it("count days from the buy session to today in New York and flag the hospital", () => {
    const lots = read().openLots;
    expect(lots.map((l) => [l.arm, l.daysHeld, l.hospital])).toEqual([
      ["decay", 31, true],
      ["once", 1, false],
    ]);
  });

  it("names the loop's last run, the session and the breaks", () => {
    const p = read();
    expect(p.dbPresent).toBe(true);
    expect(p.session).toBe("2026-11-20");
    expect(p.today).toBe("2026-11-20");
    expect(p.lastIteration).toMatchObject({ phase: "session", status: "ok" });
    expect(p.lastIteration!.ageSeconds).toBeCloseTo(90, 0);
    expect(p.measurementBreaks).toEqual([{ date: "2026-11-13", key: "arms", note: "session arm added" }]);
  });

  it("reads the decision journal under the ladder's own session", () => {
    const d = readDecisions(config, "ladder", null);
    expect(JSON.stringify(d)).toContain("above_level");
  });
});

describe("an absent ledger", () => {
  it("is an empty page, flagged absent, not an error", () => {
    const none = { paths: { ladderDir: path.join(os.tmpdir(), "console-ladder-nowhere") } } as unknown as ConsoleConfig;
    const p = readLadder(none, NOW);
    expect(p.dbPresent).toBe(false);
    expect(p.openLots).toEqual([]);
    expect(readLadderHistory(none, { arm: null }).rows).toEqual([]);
  });
});

describe("dates", () => {
  it("reads the ET date whatever the clock's zone", () => {
    expect(etDate(new Date("2026-11-20T03:00:00Z"))).toBe("2026-11-19");
    expect(daysBetween("2026-10-20", "2026-11-20")).toBe(31);
  });

  it("holds the group's 18 days where the module counts them", () => {
    const py = fs.readFileSync(path.join(__dirname, "..", "..", "..", "ladder", "src", "cherrypick", "ladder", "analytics.py"), "utf-8");
    expect(py).toMatch(new RegExp(`^HOSPITAL_DAYS = ${String(LADDER_HOSPITAL_DAYS)}\\b`, "m"));
  });
});
