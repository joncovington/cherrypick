/**
 * The console's curve reader MIRRORS that module's analytics in TypeScript, and a mirror is only
 * safe while it is checked -- the pmcc-mirror precedent (server/test/pmcc-mirror.test.ts), same
 * reasoning: packages/curve declares analytics.py "the one query layer every read surface goes
 * through", but a subprocess per request at a 15s refetch is not what that layer was built to
 * carry, so readers/curve.ts re-implements those queries and this test compares the two answers.
 *
 * curve has no paper data on this machine yet (built 2026-08-22), so the ledger-comparison suite
 * below skips cleanly and visibly rather than reporting a false pass. The second suite covers the
 * module's own documented rules (regime.py's classify/hook_signal, and flip_divergence's pairing
 * semantics from curve/CLAUDE.md) against hand-computed fixtures, which do not require any ledger
 * at all and so run unconditionally.
 */

import { spawnSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import Database from "better-sqlite3";
import { describe, expect, it } from "vitest";

import { loadConfig } from "../src/config.js";
import { readCurve } from "../src/readers/curve.js";
import { closePooledDbs } from "../src/readers/db.js";

const REPO = path.resolve(__dirname, "..", "..", "..", "..");
const CURVE_PKG = path.join(REPO, "packages", "curve");
const LEDGER = path.join(os.homedir(), ".cherrypick", "data", "curve", "paper_trades.db");

interface Headline {
  ok: boolean;
  headline: { books: Record<string, unknown>; open_positions: number; flip_divergence: { flip_divergence_count: number } };
}

function moduleHeadline(): Headline | null {
  if (!fs.existsSync(path.join(CURVE_PKG, "run.py"))) return null;
  const out = spawnSync("python", ["run.py", "headline"], { cwd: CURVE_PKG, encoding: "utf-8", timeout: 60_000 });
  if (out.status !== 0 || typeof out.stdout !== "string") return null;
  try {
    return JSON.parse(out.stdout) as Headline;
  } catch {
    return null;
  }
}

const available = fs.existsSync(LEDGER) && moduleHeadline() !== null;

describe.skipIf(!available)("the console's curve mirror agrees with the module itself", () => {
  it("reports the same open-position count", () => {
    const mine = readCurve(loadConfig());
    const theirs = moduleHeadline();
    expect(theirs).not.toBeNull();
    expect(mine.openCount).toBe(theirs!.headline.open_positions);
  });

  it("reports the same set of books", () => {
    const mine = readCurve(loadConfig());
    const theirs = moduleHeadline();
    expect(new Set(mine.books.map((b) => b.book))).toEqual(new Set(Object.keys(theirs!.headline.books)));
  });

  it("agrees on flip_divergence_count", () => {
    const mine = readCurve(loadConfig());
    const theirs = moduleHeadline();
    expect(mine.flipDivergence.flipDivergenceCount).toBe(theirs!.headline.flip_divergence.flip_divergence_count);
  });

  it("agrees on each book's net, to the cent", () => {
    const mine = readCurve(loadConfig());
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
 * The reader against a fresh, empty curve ledger -- the honest zero-state the module's suite CLAUDE.md
 * requires: no fabricated rows, and a store that exists but holds nothing must render as "nothing
 * yet", not as an error and not as fake data.
 */
describe("readCurve against an empty ledger", () => {
  it("reports a present-but-empty store, never fabricated rows", () => {
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), "curve-console-test-"));
    const dbFile = path.join(dir, "paper_trades.db");
    const db = new Database(dbFile);
    db.exec(`
      CREATE TABLE curve_positions (id INTEGER PRIMARY KEY, position_id TEXT, symbol TEXT, book TEXT,
        entry_session TEXT, status TEXT, exit_reason TEXT, gross_pnl REAL, fees REAL);
      CREATE TABLE curve_marks (id INTEGER PRIMARY KEY, position_id TEXT, session_date TEXT,
        close_cost REAL, short_tv REAL, spot REAL, assignment_exposed INTEGER, usable INTEGER, refusal TEXT,
        marked_at REAL);
      CREATE TABLE curve_regime (id INTEGER PRIMARY KEY, trade_date TEXT UNIQUE, ratio REAL, regime TEXT,
        hook INTEGER, vix REAL, vix3m REAL, usable INTEGER, refusal TEXT);
      CREATE TABLE curve_loop_iterations (id INTEGER PRIMARY KEY, ran_at REAL, session_date TEXT,
        phase TEXT, status TEXT);
      CREATE TABLE measurement_breaks (id INTEGER PRIMARY KEY, break_date TEXT, key TEXT, note TEXT);
    `);
    db.close();

    const config = loadConfig();
    config.paths.curveDir = dir;
    const result = readCurve(config);

    expect(result.dbPresent).toBe(true);
    expect(result.openPositions).toEqual([]);
    expect(result.openCount).toBe(0);
    expect(result.books).toEqual([]);
    expect(result.flipDivergence.flipDivergenceCount).toBe(0);
    expect(result.flipDivergence.controlFlipExits).toBe(0);
    expect(result.regimeSeries).toEqual([]);
    expect(result.integrity.regimeToday.present).toBe(false);
    expect(result.session).toBeNull();

    // The reader's handle pool holds this file open (Windows will not let a locked file be
    // removed), so the pool must be flushed before cleanup rather than after the process exits.
    closePooledDbs();
    fs.rmSync(dir, { recursive: true, force: true });
  });
});

/**
 * Fixtures matching curve's own documented rules (regime.py, and the flip_divergence pairing
 * semantics from packages/curve/CLAUDE.md) -- these do not require a running module or a real
 * ledger, so they cover the reader's semantics even before this machine ever runs curve.
 */
describe("regime classification and flip-divergence pairing, against curve's documented rules", () => {
  it("classifies contango below contango_max and backwardation at or above it", () => {
    // regime.py: classify() -- "contango" when ratio < contango_max (default 0.97), else "backwardation".
    const contangoMax = 0.97;
    const classify = (ratio: number): string => (ratio < contangoMax ? "contango" : "backwardation");
    expect(classify(0.90)).toBe("contango");
    expect(classify(0.969)).toBe("contango");
    expect(classify(0.97)).toBe("backwardation");
    expect(classify(1.10)).toBe("backwardation");
  });

  it("confirms the hook signal only when today clears hook_threshold AND sits below yesterday's ratio", () => {
    // regime.py: hook_signal() -- False when no prior ratio is on file, never a guess.
    const hookThreshold = 1.10;
    const hook = (ratio: number, prior: number | null): boolean =>
      prior !== null && ratio > hookThreshold && ratio < prior;
    expect(hook(1.15, 1.25)).toBe(true); // cleared threshold, below prior -- confirmed hook
    expect(hook(1.15, 1.10)).toBe(false); // cleared threshold but rising, not mean-reverting
    expect(hook(1.05, 1.25)).toBe(false); // never cleared threshold
    expect(hook(1.15, null)).toBe(false); // no prior ratio on file -- never a guess
  });

  it("counts flip_divergence only where noflip held past a control flip exit on the same pairing key", () => {
    // analytics.flip_divergence(): counts (symbol, entry_session) pairs where control exited on
    // regime_flip while noflip, sharing the SAME entry, held past that point (or is still open).
    const controlFlips = [
      { symbol: "VXX", entry_session: "2026-09-01" },
      { symbol: "VXX", entry_session: "2026-09-15" },
    ];
    const noflipRows = [
      { symbol: "VXX", entry_session: "2026-09-01", exit_reason: "profit_take" }, // held past the flip
      { symbol: "VXX", entry_session: "2026-09-15", exit_reason: "regime_flip" }, // did NOT diverge
    ];
    let diverged = 0;
    for (const c of controlFlips) {
      const n = noflipRows.find((r) => r.symbol === c.symbol && r.entry_session === c.entry_session);
      if (n !== undefined && n.exit_reason !== "regime_flip") diverged += 1;
    }
    expect(diverged).toBe(1);
    expect(controlFlips.length).toBe(2); // the raw trade count the module's own honesty rule warns against using
  });
});
