import { afterAll, beforeAll, describe, expect, it } from "vitest";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import Database from "better-sqlite3";
import type { ConsoleConfig } from "../src/config.js";
import { NO_ATTEMPTS_SCOPE, readEntryAttempts, type AttemptsPayload } from "../src/readers/attempts.js";
import { readOccupancy } from "../src/readers/occupancy.js";
import { closePooledDbs } from "../src/readers/db.js";

const SCHEMA = fs.readFileSync(path.join(__dirname, "fixtures", "flies-ledger-schema.sql"), "utf-8");

let config: ConsoleConfig;

beforeAll(() => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "console-fliesattemptsscope-"));
  fs.mkdirSync(path.join(tmp, "flies"), { recursive: true });
  const db = new Database(path.join(tmp, "flies", "paper_trades.db"));
  db.exec(SCHEMA);

  const attempt = db.prepare(
    `INSERT INTO fly_entry_attempts (ts, trade_date, arm, symbol, outcome, block_detail, center, wing_width, far_width, blocking_strike, seconds_until_cadence_clear, underlying_price)
     VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, ?)`,
  );
  attempt.run("10:00", "2026-09-25", "armA", "SPX", "filled", null, 6000, 1, 2, 6000);
  attempt.run("10:01", "2026-09-25", "armA", "SPX", "gate_blocked", "regime_gex_negative", 6000, 1, 2, 6000);
  attempt.run("10:02", "2026-09-25", "armA", "NDX", "sign_rule_blocked", "occupied", 6000, 1, 2, 6000);
  attempt.run("10:03", "2026-09-25", "armB", "SPX", "gate_blocked", "regime_gex_negative", 6000, 1, 2, 6000);
  // module-wide break (NULL arm) must remain visible
  attempt.run("10:04", "2026-09-25", null, "SPX", "module_wide_break", "test_break", 6000, 1, 2, 6000);
  const dec = db.prepare(
    `INSERT INTO fly_decisions (ts, trade_date, arm, symbol, outcome, block_detail, reason, center, wing_width, far_width, blocking_strike, seconds_until_cadence_clear, underlying_price, mode)
     VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, ?, 'cadence')`,
  );
  dec.run("10:04", "2026-09-25", null, "SPX", "module_wide_break", "test_break", "test_break", 6000, 1, 2, 6000);

  const pos = db.prepare(
    `INSERT INTO fly_positions (trade_date, arm, symbol, kind, side, center, wing_width, far_width, status, void_reason)
     VALUES (?, ?, ?, 'fly', 'call', 6000, 1, 2, 'open', NULL)`,
  );
  pos.run("2026-09-25", "armA", "SPX");
  pos.run("2026-09-25", "armB", "SPX");
  db.close();

  config = {
    port: 0,
    paths: {
      cherrypick: tmp,
      streamCacheDb: path.join(tmp, "s.db"),
      watchdogLast: path.join(tmp, "w.json"),
      orchestratorConfig: path.join(tmp, "c.json"),
      consoleData: path.join(tmp, "console"),
      meicDir: path.join(tmp, "meic"),
      fliesDir: path.join(tmp, "flies"),
      earningsDir: path.join(tmp, "earnings"),
      gexDir: path.join(tmp, "gex"),
      reviewDir: path.join(tmp, "review"),
      overviewDir: path.join(tmp, "overview"),
      advisorDir: path.join(tmp, "advisor"),
      adviceDir: path.join(tmp, "advice"),
      meicRiskConfig: path.join(tmp, "r.json"),
      fliesConfig: path.join(tmp, "f.json"),
    },
  } as unknown as ConsoleConfig;
});

afterAll(() => {
  closePooledDbs();
});

const armNames = (p: AttemptsPayload) => p.arms.map((a) => a.arm);

describe("flies attempts honours the page scope", () => {
  it("narrows timeline and rail to the selected arm", () => {
    const p = readEntryAttempts(config, "flies", "paper", null, { ...NO_ATTEMPTS_SCOPE, arm: "armA" });
    expect(armNames(p)).toContain("armA");
    expect(p.timeline.some((r) => r.arm === "armB")).toBe(false);
  });

  it("narrows to the selected symbol", () => {
    const p = readEntryAttempts(config, "flies", "paper", null, { ...NO_ATTEMPTS_SCOPE, symbol: "NDX" });
    expect(p.timeline.every((r) => r.symbol === "NDX")).toBe(true);
  });

  it("module-wide break (NULL arm) remains visible under arm scope", () => {
    const p = readEntryAttempts(config, "flies", "paper", null, { ...NO_ATTEMPTS_SCOPE, arm: "armA" });
    expect(p.breaks.some((b) => b.reason === "test_break") || p.timeline.some((r) => r.outcome === "module_wide_break")).toBe(true);
  });

  it("occupancy honours arm scope", () => {
    const unscoped = readOccupancy(config, "flies", "paper", null, NO_ATTEMPTS_SCOPE);
    expect(unscoped.tradeDate).toBe("2026-09-25");
    expect(unscoped.legs.some((l) => l.arm === "armB")).toBe(true);
    const occ = readOccupancy(config, "flies", "paper", null, { ...NO_ATTEMPTS_SCOPE, arm: "armA" });
    expect(occ.tradeDate).toBe("2026-09-25");
    expect(occ.legs.length).toBeGreaterThan(0);
    expect(occ.legs.every((l) => l.arm === "armA")).toBe(true);
  });
});
