import { afterAll, beforeAll, describe, expect, it } from "vitest";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import Database from "better-sqlite3";
import type { ConsoleConfig } from "../src/config.js";
import { clearMemoOnStore, closePooledDbs } from "../src/readers/db.js";
import { countGroups, readAttemptCounts, readEntryAttempts } from "../src/readers/attempts.js";

/**
 * The Overview's entries row counted the attempts page's TIMELINE, which is thinned for its chart to
 * one mark per arm, outcome and minute. An arm refused on every 15s tick therefore counted once a
 * minute: on 2026-10-12 flies showed 6,771 refused against 26,272 in the ledger. The counts now come
 * from one grouped query over the full day, so thinning the chart can never move them.
 */

const SCHEMA = fs.readFileSync(path.join(__dirname, "fixtures", "meic-ledger-schema.sql"), "utf-8");

let config: ConsoleConfig;
let dbPath: string;

beforeAll(() => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "console-attemptcounts-"));
  fs.mkdirSync(path.join(tmp, "meic"), { recursive: true });
  dbPath = path.join(tmp, "meic", "paper_trades.db");
  const db = new Database(dbPath);
  db.exec(SCHEMA);
  const attempt = db.prepare(
    `INSERT INTO entry_attempts (ts, trade_date, arm, symbol, outcome, block_detail, put_strike, call_strike,
       wing_width, blocking_strike, seconds_until_cadence_clear, underlying_price)
     VALUES (?, ?, ?, ?, ?, ?, 5950, 6050, 5, NULL, NULL, 6000)`,
  );
  // armA: refused on every 15s tick for ten minutes -- 40 rows the chart draws as 10 marks.
  for (let i = 0; i < 40; i++) {
    const s = i * 15;
    const ts = `10:${String(Math.floor(s / 60)).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`;
    attempt.run(ts, "2026-09-25", "armA", "SPX", "gate_blocked", "regime_gex_negative");
  }
  attempt.run("10:20:00", "2026-09-25", "armA", "SPX", "filled", null);
  attempt.run("10:21:00", "2026-09-25", "armB", "SPX", "no_fill", null);
  attempt.run("10:21:15", "2026-09-25", "armB", "SPX", "no_fill", null);
  attempt.run("10:22:00", "2026-09-25", "armB", "NDX", "cadence_blocked", null);
  // An earlier session, for the rail's lifetime counts.
  attempt.run("10:00:00", "2026-09-24", "armA", "SPX", "gate_blocked", "regime_gex_negative");
  attempt.run("10:00:00", "2026-09-23", "armA", "NDX", "filled", null);
  attempt.run("10:00:00", "2026-09-23", "armB", "SPX", "filled", null);
  db.close();

  config = {
    port: 0,
    paths: { cherrypick: tmp, meicDir: path.join(tmp, "meic") },
  } as unknown as ConsoleConfig;
});

afterAll(() => {
  closePooledDbs();
  clearMemoOnStore();
});

describe("readAttemptCounts", () => {
  it("counts every refusal on the day, however thin the chart's timeline is", () => {
    const page = readEntryAttempts(config, "meic", "paper", null);
    const refusalMarks = page.timeline.filter((r) => r.outcome === "gate_blocked").length;
    expect(refusalMarks).toBe(10); // thinned: one mark per minute

    const counts = readAttemptCounts(config, "meic", "paper", null);
    expect(counts.tradeDate).toBe("2026-09-25");
    expect(counts.filled).toBe(1);
    expect(counts.noFill).toBe(2);
    expect(counts.refused).toBe(41); // 40 gate refusals + 1 cadence, not 10 + 1
    expect(counts.topRefusal).toBe("regime_gex_negative ×40");
  });

  it("agrees with the arm rail, which has always counted the full rows", () => {
    const page = readEntryAttempts(config, "meic", "paper", null);
    const counts = readAttemptCounts(config, "meic", "paper", null);
    const railRefused = page.arms.reduce((s, a) => s + Object.values(a.refusals).reduce((x, n) => x + n, 0), 0);
    const railNoFill = page.arms.reduce((s, a) => s + (a.refusals["no_fill"] ?? 0), 0);
    expect(counts.refused).toBe(railRefused - railNoFill);
    expect(counts.filled).toBe(page.arms.reduce((s, a) => s + a.fills, 0));
  });

  it("reads an asked-for day, and nothing for a day with no rows", () => {
    expect(readAttemptCounts(config, "meic", "paper", "2026-09-24")).toEqual({
      tradeDate: "2026-09-24",
      filled: 0,
      refused: 1,
      noFill: 0,
      topRefusal: "regime_gex_negative ×1",
    });
    expect(readAttemptCounts(config, "meic", "paper", "2026-01-02").refused).toBe(0);
  });

  it("keys a refusal by its detail, else its outcome, and breaks ties by first appearance", () => {
    const c = countGroups("d", [
      { outcome: "window_blocked", block_detail: "before_open_gate", n: 3, first: 1 },
      { outcome: "cadence_blocked", block_detail: null, n: 3, first: 5 },
      { outcome: "gate_blocked", block_detail: "before_open_gate", n: 1, first: 9 },
      { outcome: null, block_detail: null, n: 2, first: 7 },
    ]);
    expect(c.refused).toBe(9);
    expect(c.topRefusal).toBe("before_open_gate ×4");
    expect(countGroups("d", [
      { outcome: "cadence_blocked", block_detail: null, n: 3, first: 5 },
      { outcome: "gate_blocked", block_detail: "x", n: 3, first: 2 },
    ]).topRefusal).toBe("x ×3");
  });
});

describe("the rail's lifetime session counts", () => {
  it("match the single grouped scan they replaced, scoped and unscoped", () => {
    const db = new Database(dbPath, { readonly: true });
    const old = (where: string, ...params: string[]) =>
      db
        .prepare(
          `SELECT arm, COUNT(DISTINCT trade_date) AS seen,
                  COUNT(DISTINCT CASE WHEN outcome = 'filled' THEN trade_date END) AS filled
             FROM entry_attempts ${where} GROUP BY 1`,
        )
        .all(...params) as Array<{ arm: string; seen: number; filled: number }>;
    const rail = (scope: { arm: string | null; symbol: string | null }) =>
      Object.fromEntries(
        readEntryAttempts(config, "meic", "paper", "2026-09-25", scope).arms.map((a) => [
          a.arm,
          { seen: a.sessionsSeen, filled: a.sessionsWithFills },
        ]),
      );
    const want = (rows: Array<{ arm: string; seen: number; filled: number }>, arms: string[]) =>
      Object.fromEntries(rows.filter((r) => arms.includes(r.arm)).map((r) => [r.arm, { seen: r.seen, filled: r.filled }]));

    expect(rail({ arm: null, symbol: null })).toEqual(want(old(""), ["armA", "armB"]));
    expect(rail({ arm: null, symbol: "SPX" })).toEqual(want(old("WHERE symbol = ?", "SPX"), ["armA", "armB"]));
    expect(rail({ arm: "armB", symbol: "NDX" })).toEqual(
      want(old("WHERE arm = ? AND symbol = ?", "armB", "NDX"), ["armB"]),
    );
    db.close();
  });
});
