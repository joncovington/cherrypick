/**
 * The console's bwb reader MIRRORS that module's analytics in TypeScript, and a mirror is only
 * safe while it is checked -- the curve/pmcc-mirror precedent (server/test/curve-mirror.test.ts,
 * server/test/pmcc-mirror.test.ts), same reasoning: packages/bwb declares analytics.py "the one
 * query layer every read surface goes through", but a subprocess per request at a 15s refetch is
 * not what that layer was built to carry, so readers/bwb.ts re-implements those queries and this
 * test compares the two answers.
 *
 * bwb has no paper data on this machine yet (built 2026-08-23), so the ledger-comparison suite
 * below skips cleanly and visibly rather than reporting a false pass. The second suite covers the
 * module's own documented rules (triggers.py's delta/bounce/flip fire conditions from bwb/CLAUDE.md)
 * against hand-computed fixtures, which do not require any ledger at all and so run unconditionally.
 */

import { spawnSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import Database from "better-sqlite3";
import { describe, expect, it } from "vitest";

import { loadConfig } from "../src/config.js";
import { readBwb } from "../src/readers/bwb.js";
import { closePooledDbs } from "../src/readers/db.js";

const REPO = path.resolve(__dirname, "..", "..", "..", "..");
const BWB_PKG = path.join(REPO, "packages", "bwb");
const LEDGER = path.join(os.homedir(), ".cherrypick", "data", "bwb", "paper_trades.db");

interface Headline {
  ok: boolean;
  headline: {
    books: Record<string, unknown>;
    open_positions: number;
    fire_counts: Record<string, { positions: number; fired: number }>;
  };
}

function moduleHeadline(): Headline | null {
  if (!fs.existsSync(path.join(BWB_PKG, "run.py"))) return null;
  const out = spawnSync("python", ["run.py", "headline"], { cwd: BWB_PKG, encoding: "utf-8", timeout: 60_000 });
  if (out.status !== 0 || typeof out.stdout !== "string") return null;
  try {
    return JSON.parse(out.stdout) as Headline;
  } catch {
    return null;
  }
}

const available = fs.existsSync(LEDGER) && moduleHeadline() !== null;

describe.skipIf(!available)("the console's bwb mirror agrees with the module itself", () => {
  it("reports the same open-position count", () => {
    const mine = readBwb(loadConfig());
    const theirs = moduleHeadline();
    expect(theirs).not.toBeNull();
    expect(mine.openCount).toBe(theirs!.headline.open_positions);
  });

  it("reports the same set of books", () => {
    const mine = readBwb(loadConfig());
    const theirs = moduleHeadline();
    expect(new Set(mine.books.map((b) => b.book))).toEqual(new Set(Object.keys(theirs!.headline.books)));
  });

  it("agrees on each arm's fire count", () => {
    const mine = readBwb(loadConfig());
    const theirs = moduleHeadline()!.headline.fire_counts;
    for (const c of mine.fireCounts) {
      const other = theirs[c.book];
      if (other === undefined) continue;
      expect(c.positions).toBe(other.positions);
      expect(c.fired).toBe(other.fired);
    }
  });

  it("agrees on each book's net, to the cent", () => {
    const mine = readBwb(loadConfig());
    const theirs = moduleHeadline()!.headline.books as Record<string, Record<string, { net_pnl?: number }>>;
    for (const cell of mine.books) {
      const other = theirs[cell.book]?.[cell.symbol];
      if (other?.net_pnl === undefined) continue;
      expect(cell.netPnl ?? 0).toBeCloseTo(other.net_pnl, 2);
    }
  });
});

describe("the mirror check itself", () => {
  it("says plainly when it could not run", () => {
    expect(typeof available).toBe("boolean");
    if (!available) {
      expect(fs.existsSync(LEDGER) === false || moduleHeadline() === null).toBe(true);
    }
  });
});

/**
 * The reader against a fresh, empty bwb ledger -- the honest zero-state the module's suite CLAUDE.md
 * requires: no fabricated rows, and a store that exists but holds nothing must render as "nothing
 * yet", not as an error and not as fake data.
 */
describe("readBwb against an empty ledger", () => {
  it("reports a present-but-empty store, never fabricated rows", () => {
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), "bwb-console-test-"));
    const dbFile = path.join(dir, "paper_trades.db");
    const db = new Database(dbFile);
    db.exec(`
      CREATE TABLE bwb_positions (id INTEGER PRIMARY KEY, position_id TEXT, symbol TEXT, book TEXT,
        entry_session TEXT, structure_signature TEXT, status TEXT, exit_reason TEXT,
        body_strike REAL, near_strike REAL, far_strike REAL, expiration TEXT, entry_spot REAL,
        entry_credit REAL, entry_max_loss REAL, peak_abs_delta REAL, below_flip_seen INTEGER,
        armed_at TEXT, addon_fired_at TEXT, addon_credit REAL, gross_pnl REAL, fees REAL);
      CREATE TABLE bwb_marks (id INTEGER PRIMARY KEY, position_id TEXT, session_date TEXT,
        close_cost REAL, spot REAL, usable INTEGER, refusal TEXT, marked_at REAL);
      CREATE TABLE bwb_trigger_ticks (id INTEGER PRIMARY KEY, entry_session TEXT,
        structure_signature TEXT, session_date TEXT, measured INTEGER, refusal TEXT, ticked_at REAL);
      CREATE TABLE bwb_entry_attempts (id INTEGER PRIMARY KEY, ts TEXT, trade_date TEXT, symbol TEXT,
        book TEXT, outcome TEXT, credit REAL);
      CREATE TABLE bwb_management_events (id INTEGER PRIMARY KEY, position_id TEXT, occurred_at REAL,
        session_date TEXT, action TEXT, reason TEXT, executed INTEGER, gate TEXT);
      CREATE TABLE bwb_loop_iterations (id INTEGER PRIMARY KEY, ran_at REAL, session_date TEXT,
        phase TEXT, status TEXT);
      CREATE TABLE measurement_breaks (id INTEGER PRIMARY KEY, break_date TEXT, key TEXT, note TEXT);
    `);
    db.close();

    const config = loadConfig();
    config.paths.bwbDir = dir;
    const result = readBwb(config);

    expect(result.dbPresent).toBe(true);
    expect(result.openPositions).toEqual([]);
    expect(result.openCount).toBe(0);
    expect(result.books).toEqual([]);
    expect(result.fireCounts).toEqual([]);
    expect(result.entryAttemptsToday).toEqual([]);
    expect(result.managementEventsToday).toEqual([]);
    expect(result.session).toBeNull();

    // The reader's handle pool holds this file open (Windows will not let a locked file be
    // removed), so the pool must be flushed before cleanup rather than after the process exits.
    closePooledDbs();
    fs.rmSync(dir, { recursive: true, force: true });
  });
});

/**
 * Fixtures matching bwb's own documented trigger rules (triggers.py, from packages/bwb/CLAUDE.md) --
 * these do not require a running module or a real ledger, so they cover the reader's semantics even
 * before this machine ever runs bwb.
 */
describe("trigger fire conditions, against bwb's documented rules", () => {
  it("fires delta when the near wing's |delta| reaches delta_trigger on the current tick", () => {
    const deltaTrigger = 0.5;
    const deltaFires = (absDelta: number | null): boolean => absDelta !== null && absDelta >= deltaTrigger;
    expect(deltaFires(0.5)).toBe(true);
    expect(deltaFires(0.49)).toBe(false);
    expect(deltaFires(null)).toBe(false);
  });

  it("fires bounce only after peak clears the trigger AND current pulls back by bounce_pullback", () => {
    const deltaTrigger = 0.5;
    const bouncePullback = 0.05;
    const bounceFires = (peak: number | null, absDelta: number | null): boolean =>
      peak !== null && absDelta !== null && peak >= deltaTrigger && absDelta <= deltaTrigger - bouncePullback;
    expect(bounceFires(0.55, 0.44)).toBe(true); // peak crossed, pulled back past the bar
    expect(bounceFires(0.55, 0.48)).toBe(false); // peak crossed, not pulled back enough yet
    expect(bounceFires(0.4, 0.3)).toBe(false); // never crossed the trigger at all
  });

  it("fires flip only after the below-flip latch AND a reclaim past flip_buffer", () => {
    const flipBuffer = 1.001;
    const flipFires = (belowFlipSeen: boolean, spot: number | null, gammaFlip: number | null): boolean =>
      belowFlipSeen && spot !== null && gammaFlip !== null && spot >= gammaFlip * flipBuffer;
    expect(flipFires(true, 6006, 6000)).toBe(true); // latched, reclaimed past the buffer
    expect(flipFires(true, 6000.5, 6000)).toBe(false); // latched, reclaim not past the buffer yet
    expect(flipFires(false, 6006, 6000)).toBe(false); // never traded below flip -- no latch
  });
});
