/**
 * The console's PMCC era is a literal copy of the module's (this package cannot import Python), so
 * it is pinned here: when `analytics.CURRENT_ERA` moves at a boundary and this copy does not, every
 * arm-comparison figure on the page reads the old era while the module's own headline reads the
 * new one, and both look correct. The scoping itself is checked against a hand-built ledger.
 */
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import Database from "better-sqlite3";
import { describe, expect, it } from "vitest";

import { loadConfig } from "../src/config.js";
import { CURRENT_ERA, readPmcc } from "../src/readers/pmcc.js";

const ANALYTICS = path.resolve(__dirname, "..", "..", "..", "pmcc", "src", "cherrypick", "pmcc", "analytics.py");
const SCHEMA = path.join(__dirname, "fixtures", "pmcc-ledger-schema.sql");

describe("the PMCC era", () => {
  it("is the module's own CURRENT_ERA", () => {
    const match = /^CURRENT_ERA = "([^"]+)"$/m.exec(fs.readFileSync(ANALYTICS, "utf-8"));
    expect(match?.[1]).toBeDefined();
    expect(CURRENT_ERA).toBe(match?.[1]);
  });

  it("scopes the arm comparison to it by default, reaches an earlier era by name, and pools only when asked", () => {
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), "pmcc-era-"));
    const db = new Database(path.join(dir, "paper_trades.db"));
    db.exec(fs.readFileSync(SCHEMA, "utf-8"));
    const insert = db.prepare(
      `INSERT INTO pmcc_positions (position_id, symbol, arm, status, entry_session, gross_pnl, fees, roll_count, era,
                                   long_expiration, long_strike, short_expiration, short_strike)
       VALUES (?, ?, 'control', 'closed', ?, ?, 1, 0, ?, '2026-12-18', 100, '2026-12-11', 110)`,
    );
    insert.run("XSP:control:2026-09-01", "XSP", "2026-09-01", 50, "redesign");
    insert.run("XSP:control:2026-10-05", "XSP", "2026-10-05", 20, CURRENT_ERA);
    insert.run("TQQQ:control:2026-08-10", "TQQQ", "2026-08-10", 7, null);
    db.close();
    const base = loadConfig();
    const config = { ...base, paths: { ...base.paths, pmccDir: dir } };
    const net = (era: string | null) =>
      readPmcc(config, era)
        .arms.map((a) => `${a.symbol}:${String(a.netPnl)}`)
        .sort();
    expect(net(null)).toEqual(["XSP:19"]);
    expect(net("redesign")).toEqual(["XSP:49"]);
    expect(net("pre-redesign")).toEqual(["TQQQ:6"]);
    expect(net("ALL")).toEqual(["TQQQ:6", "XSP:68"]);
    expect(readPmcc(config, null).eras.map((e) => [e.era, e.trades])).toEqual([
      [CURRENT_ERA, 1],
      ["redesign", 1],
      ["pre-redesign", 1],
    ]);
  });
});
