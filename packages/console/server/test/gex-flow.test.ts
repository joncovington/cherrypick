import { describe, it, expect } from "vitest";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import Database from "better-sqlite3";
import { flowAdjusted, readSignedFlow } from "../src/analytics/gexFlow.js";

/**
 * The SQL mirror of `core.gex.signed_flow` / `flow_adjusted`, pinned to the same hand-worked fixture
 * core's `tests/test_gex_flow.py` reads, so the panel and the core cannot classify a print differently.
 */

const here = path.dirname(fileURLToPath(import.meta.url));
const CASES = JSON.parse(
  fs.readFileSync(path.join(here, "..", "..", "..", "core", "tests", "fixtures", "gex_flow_cases.json"), "utf-8"),
) as {
  spot: number;
  multiplier: number;
  strikes: Array<{ strike: number; gamma: number; call_oi: number; put_oi: number }>;
  prints: Array<Record<string, unknown>>;
  expected_flow: unknown;
  expected_adjusted: Array<Record<string, unknown>>;
};

const strip = (rows: Array<Record<string, unknown>>) =>
  rows.map((r) => Object.fromEntries(Object.entries(r).filter(([k]) => !k.startsWith("_"))));

/** gex's `gex_prints` (packages/gex service.py), seeded with the fixture's prints. */
function seeded(): Database.Database {
  const db = new Database(":memory:");
  db.exec(
    `CREATE TABLE gex_prints (symbol TEXT NOT NULL, trade_date TEXT NOT NULL, expiration TEXT NOT NULL,
       side INTEGER NOT NULL, strike REAL NOT NULL, idx INTEGER NOT NULL, event_ms INTEGER, received_at REAL NOT NULL,
       price REAL, size INTEGER, bid REAL, ask REAL, aggressor TEXT, spread_leg INTEGER, eth INTEGER,
       valid_tick INTEGER, kind TEXT NOT NULL, conditions TEXT, exchange TEXT,
       PRIMARY KEY (symbol, expiration, side, strike, idx)) WITHOUT ROWID`,
  );
  const insert = db.prepare(
    `INSERT INTO gex_prints (symbol, trade_date, expiration, side, strike, idx, received_at, price, size, bid, ask,
       aggressor, spread_leg, eth, valid_tick, kind) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)`,
  );
  CASES.prints.forEach((p, i) => {
    insert.run("SPX", "D", "E", p["side"] === "call" ? 1 : 0, p["strike"], i, i, p["price"], p["size"], p["bid"],
      p["ask"], p["aggressor"], p["spread_leg"], p["eth"], p["valid_tick"], p["kind"]);
  });
  // Another session and another expiration, which must not be counted.
  insert.run("SPX", "OTHER", "E", 1, 100, 99, 0, 2, 50, 1.9, 2, "BUY", 0, 0, 1, "NEW");
  insert.run("SPX", "D", "OTHER", 1, 100, 98, 0, 2, 50, 1.9, 2, "BUY", 0, 0, 1, "NEW");
  return db;
}

describe("signed flow (mirror of core.gex)", () => {
  it("classifies and sums the fixture's prints as the core does", () => {
    expect(readSignedFlow(seeded(), "SPX", "E", "D")).toEqual(CASES.expected_flow);
  });

  it("adjusts the profile by the net bought as the core does", () => {
    const flow = readSignedFlow(seeded(), "SPX", "E", "D").strikes;
    expect(flowAdjusted(CASES.strikes, flow, CASES.spot, CASES.multiplier)).toEqual(strip(CASES.expected_adjusted));
  });

  it("is empty for a session with no prints", () => {
    const f = readSignedFlow(seeded(), "SPX", "E", "NONE");
    expect(f.strikes).toEqual([]);
    expect(f.classified_by).toEqual({ aggressor: 0, quote: 0, unclassified: 0 });
  });
});
