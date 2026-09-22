import { describe, it, expect, beforeEach, afterEach } from "vitest";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import Database from "better-sqlite3";
import type { ConsoleConfig } from "../src/config.js";
import { readOpeningRange } from "../src/readers/openingRange.js";
import { closePooledDbs } from "../src/readers/db.js";

/**
 * The opening-range reader: bucketing and min/max over the gex recorder's spot trail, and nothing
 * else. What is pinned here is the honesty of an incomplete window — a range over four of six
 * buckets is not a range, and returning zero for it would put a quietly wrong number on screen at
 * the moment entries begin.
 */

let tmp: string;
let config: ConsoleConfig;

function cfg(root: string): ConsoleConfig {
  fs.mkdirSync(path.join(root, "gex"), { recursive: true });
  return { paths: { gexDir: path.join(root, "gex") } } as unknown as ConsoleConfig;
}

/** Epoch seconds for an ET wall-clock time on a session. -04:00 is EDT, which September is in. */
function ts(session: string, hh: number, mm: number, ss = 0): number {
  return Date.parse(`${session}T${String(hh).padStart(2, "0")}:${String(mm).padStart(2, "0")}:${String(ss).padStart(2, "0")}-04:00`) / 1000;
}

function seed(rows: Array<[string, number, number]>) {
  const db = new Database(path.join(tmp, "gex", "gex_history.db"));
  db.exec("CREATE TABLE gex_spot_history (symbol TEXT, trade_date TEXT, ts REAL, spot REAL)");
  const ins = db.prepare("INSERT INTO gex_spot_history VALUES ('SPX', ?, ?, ?)");
  for (const [session, t, spot] of rows) ins.run(session, t, spot);
  db.close();
}

/** All six buckets present, walking `prices` one per bucket. */
function fullWindow(session: string, prices: number[]): Array<[string, number, number]> {
  return prices.map((p, i) => [session, ts(session, 9, 30 + i * 5, 10), p] as [string, number, number]);
}

beforeEach(() => {
  tmp = fs.mkdtempSync(path.join(os.tmpdir(), "console-openrange-"));
  config = cfg(tmp);
});

afterEach(() => {
  closePooledDbs();
  fs.rmSync(tmp, { recursive: true, force: true });
});

describe("readOpeningRange", () => {
  it("buckets the window into five-minute bars and reports the range", () => {
    seed(fullWindow("2026-09-21", [100, 101, 104, 103, 102, 105]));
    const out = readOpeningRange(config, "2026-09-21");
    expect(out.complete).toBe(true);
    expect(out.bucketsPresent).toBe(6);
    expect(out.buckets.map((b) => b.minute)).toEqual([570, 575, 580, 585, 590, 595]);
    expect(out.high).toBe(105);
    expect(out.low).toBe(100);
    expect(out.rangePoints).toBe(5);
    expect(out.first).toBe(100);
    expect(out.last).toBe(105);
  });

  it("ignores ticks outside 09:30-10:00", () => {
    const rows = fullWindow("2026-09-21", [100, 100, 100, 100, 100, 100]);
    rows.push(["2026-09-21", ts("2026-09-21", 10, 5), 999]); // after the entry window opens
    rows.push(["2026-09-21", ts("2026-09-21", 9, 15), 1]); // before the bell
    seed(rows);
    const out = readOpeningRange(config, "2026-09-21");
    expect(out.high).toBe(100);
    expect(out.low).toBe(100);
    expect(out.rangePoints).toBe(0);
  });

  it("reports an incomplete window as incomplete, never as a zero range", () => {
    // five of six buckets -- the 2026-08-17 shape
    seed(fullWindow("2026-08-17", [100, 101, 102, 103, 104]));
    const out = readOpeningRange(config, "2026-08-17");
    expect(out.complete).toBe(false);
    expect(out.rangePoints).toBeNull();
    expect(out.high).toBeNull();
    expect(out.bucketsPresent).toBe(5);
    expect(out.reason).toContain("5 of 6");
  });

  it("reports a session the recorder missed as absent, not empty", () => {
    seed(fullWindow("2026-09-21", [100, 101, 102, 103, 104, 105]));
    const out = readOpeningRange(config, "2026-07-23");
    expect(out.complete).toBe(false);
    expect(out.reason).toContain("no spot trail");
    expect(out.buckets).toEqual([]);
  });

  it("is absent rather than throwing when the gex store does not exist", () => {
    const out = readOpeningRange(config, "2026-09-21");
    expect(out.complete).toBe(false);
    expect(out.rangePoints).toBeNull();
  });

  it("carries no classified measure — that lives in core.openingrange alone", () => {
    seed(fullWindow("2026-09-21", [100, 101, 102, 103, 104, 105]));
    const out = readOpeningRange(config, "2026-09-21") as unknown as Record<string, unknown>;
    for (const key of ["orAtr", "or_atr", "efficiency", "regime", "position", "atr"]) {
      expect(out[key]).toBeUndefined();
    }
  });
});
