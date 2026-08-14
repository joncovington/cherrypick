import { describe, it, expect, beforeAll } from "vitest";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import Database from "better-sqlite3";
import type { ConsoleConfig } from "../src/config.js";
import { readMeicForest } from "../src/readers/meic.js";

/**
 * The as-entered forest prices every trade as if it were held to expiry, which for MEIC is the one
 * thing that reliably does not happen — stop management is the strategy. Read alone, its wing losses
 * look like risk the book ran. These pin the counts and the realised net that sit beside the curve
 * to say otherwise, and pin that "realised" means the same `pnl - fees` every other MEIC surface
 * reports, so two screens can never quote different money for the same session.
 */

let config: ConsoleConfig;
const DAY = "2026-08-13";

beforeAll(() => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "console-meicforest-"));
  const dir = path.join(tmp, "meic");
  fs.mkdirSync(dir, { recursive: true });
  const db = new Database(path.join(dir, "paper_trades.db"));
  db.exec(`
    CREATE TABLE ic_trades (
      id INTEGER PRIMARY KEY, ic_order_id TEXT, trade_date TEXT, risk_profile TEXT, symbol TEXT,
      put_strike REAL, call_strike REAL, wing_width REAL, net_credit REAL, quantity INTEGER,
      status TEXT, underlying_price_entry REAL, pnl REAL, fees REAL, entry_time TEXT, exit_reason TEXT
    );
  `);
  const ins = db.prepare(
    `INSERT INTO ic_trades (ic_order_id, trade_date, risk_profile, symbol, put_strike, call_strike,
                            wing_width, net_credit, quantity, status, underlying_price_entry, pnl, fees)
     VALUES (?, ?, ?, 'SPX', 7750, 7810, 10, 2.0, 1, ?, 7780, ?, ?)`,
  );
  // A profile that stops out of almost everything, and one that holds to expiry. Both matter: the
  // curve is a counterfactual for the first and a fair record for the second.
  ins.run("s1", DAY, "width-10", "stopped", -120, 5);
  ins.run("s2", DAY, "width-10", "stopped", -80, 5);
  ins.run("s3", DAY, "width-10", "expired", 200, 5);
  ins.run("o1", DAY, "open", "expired", 150, 5);
  ins.run("o2", DAY, "open", "expired", 150, 5);
  // Cancelled never became a position and must not reach the as-entered view at all.
  ins.run("x1", DAY, "open", "cancelled", null, 0);
  db.close();

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
      scoutDir: path.join(tmp, "scout"),
      reviewDir: path.join(tmp, "review"),
      advisorDir: path.join(tmp, "advisor"),
      adviceDir: path.join(tmp, "state", "advice"),
      meicRiskConfig: path.join(tmp, "config.risk.json"),
      fliesConfig: path.join(tmp, "config", "flies.json"),
    },
  };
});

const asEntered = (profile: string) =>
  readMeicForest(config, "paper", null).asEntered.find((a) => a.profile === profile)!;

describe("what the as-entered curve is standing next to", () => {
  it("counts how each trade actually ended", () => {
    expect(asEntered("width-10").outcome).toMatchObject({ entered: 3, stopped: 2, expired: 1, open: 0 });
  });

  it("reports realised P&L net of fees, the same net every other MEIC surface uses", () => {
    // (-120 - 5) + (-80 - 5) + (200 - 5)
    expect(asEntered("width-10").outcome.realisedNet).toBeCloseTo(-15, 6);
    // (150 - 5) * 2
    expect(asEntered("open").outcome.realisedNet).toBeCloseTo(290, 6);
  });

  it("a profile that stopped nothing is a fair record, not a counterfactual", () => {
    // The card leans on this to avoid claiming the wings are unrealised risk for every profile.
    expect(asEntered("open").outcome).toMatchObject({ stopped: 0, entered: 2, expired: 2 });
  });

  it("a cancelled order never entered, so it is not in the view or the counts", () => {
    const open = asEntered("open");
    expect(open.positions).toHaveLength(2);
    expect(open.outcome.entered).toBe(2);
  });

  it("the realised total is dwarfed by the curve it sits against — which is the point", () => {
    const arm = asEntered("width-10");
    expect(Math.min(...arm.pnl)).toBeLessThan(arm.outcome.realisedNet * 10);
    expect(readMeicForest(config, "paper", null).openPositions).toBe(0);
  });
});
