import { describe, it, expect, afterEach } from "vitest";
import path from "node:path";
import fs from "node:fs";
import os from "node:os";
import Database from "better-sqlite3";
import { resolvePmccSession } from "../src/readers/pmcc.js";
import { readEntryAttempts } from "../src/readers/attempts.js";
import { closePooledDbs, clearMemoOnStore } from "../src/readers/db.js";
import type { ConsoleConfig } from "../src/config.js";

/**
 * The 2026-09 PMCC incident: the "entry attempts today" card (scoped to `latestSession` -- the
 * loop's last RUN) read empty while the timeline card beside it (`/api/pmcc/attempts`, which used
 * to resolve its own date as `MAX(trade_date)` over `pmcc_entry_attempts` alone) showed a filled
 * entry dated days earlier. Both were individually correct; together they read as a contradiction,
 * because PMCC's loop ticks a few times a day rather than continuously, so a run that evaluated
 * nothing new advances the session with no new attempt row -- exactly the failure
 * `latestSession`'s own docstring already names and was written to prevent for pmcc.ts's OTHER
 * cards. `resolvePmccSession` exposes that same resolution to the attempts route; this pins that
 * the fix actually changes what the route returns, not just that the function compiles.
 */

function fakeConfig(pmccDir: string): ConsoleConfig {
  return {
    port: 0,
    paths: {
      cherrypick: pmccDir,
      streamCacheDb: "",
      watchdogLast: "",
      orchestratorConfig: path.join(pmccDir, "..", "config.json"),
      consoleData: "",
      meicDir: "",
      fliesDir: "",
      earningsDir: "",
      calendarsDir: "",
      pmccDir,
      curveDir: "",
      bwbDir: "",
      gexDir: "",
      overviewDir: "",
      reviewDir: "",
      advisorDir: "",
      streamerDir: "",
    },
  } as unknown as ConsoleConfig;
}

function tmpPmccDb(): { config: ConsoleConfig; dbFile: string } {
  const home = fs.mkdtempSync(path.join(os.tmpdir(), "console-pmcc-attempts-"));
  const pmccDir = path.join(home, "pmcc");
  fs.mkdirSync(pmccDir, { recursive: true });
  const dbFile = path.join(pmccDir, "paper_trades.db");
  const db = new Database(dbFile);
  db.exec(`
    CREATE TABLE pmcc_loop_iterations (session_date TEXT, ran_at REAL, phase TEXT, status TEXT);
    CREATE TABLE pmcc_positions (entry_session TEXT);
    CREATE TABLE pmcc_entry_attempts (
      id INTEGER PRIMARY KEY, ts REAL, trade_date TEXT, book TEXT, symbol TEXT,
      outcome TEXT, block_detail TEXT, short_strike REAL, spot REAL, best_yield REAL
    );
  `);
  // The loop ran TODAY and found nothing to evaluate -- no row lands in pmcc_entry_attempts for
  // this date. The last time an attempt actually happened was three days earlier.
  db.prepare("INSERT INTO pmcc_loop_iterations (session_date, ran_at, phase, status) VALUES (?, ?, ?, ?)").run(
    "2026-09-02",
    1_756_800_000,
    "entry",
    "ok",
  );
  db.prepare(
    "INSERT INTO pmcc_entry_attempts (ts, trade_date, book, symbol, outcome, block_detail, short_strike, spot) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
  ).run(1_756_540_000, "2026-08-27", "control", "TQQQ", "filled", null, 85, 86.4);
  db.close();
  return { config: fakeConfig(pmccDir), dbFile };
}

afterEach(() => {
  closePooledDbs();
  clearMemoOnStore();
});

describe("resolvePmccSession", () => {
  it("returns the loop's last session, not the attempts table's own last row", () => {
    const { config } = tmpPmccDb();
    expect(resolvePmccSession(config)).toBe("2026-09-02");
  });
});

describe("the pmcc attempts route's date fallback", () => {
  it("reads the loop's session (empty today), not readEntryAttempts' own MAX(trade_date) fallback", () => {
    const { config } = tmpPmccDb();
    const resolved = resolvePmccSession(config);
    const payload = readEntryAttempts(config, "pmcc", "paper", resolved);
    expect(payload.tradeDate).toBe("2026-09-02");
    expect(payload.timeline).toEqual([]);
  });

  it("sabotage check: readEntryAttempts' own unscoped fallback lands on the stale attempt instead", () => {
    // Confirms the two really do disagree -- the fix wouldn't be visible in a fixture where
    // MAX(trade_date) already happened to equal the loop's session.
    const { config } = tmpPmccDb();
    const unscoped = readEntryAttempts(config, "pmcc", "paper", null);
    expect(unscoped.tradeDate).toBe("2026-08-27");
    expect(unscoped.timeline).toHaveLength(1);
  });
});
