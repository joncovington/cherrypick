import { describe, it, expect, beforeEach } from "vitest";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import Database from "better-sqlite3";
import type { ConsoleConfig } from "../src/config.js";
import { readFliesForest } from "../src/readers/flies.js";

/**
 * A cancelled entry is not a position, and the console's forest must not draw one.
 *
 * The live session of 2026-09-23 wrote seven rows: five entries that filled and two whose entry
 * order was pulled before it filled (the 7720 and 7725 call verticals). The forest drew all seven,
 * so it showed three stranded verticals where the broker held one, and credited the book with
 * $528 of credit it never collected. The module's Python side was fixed first (`fly.held`); this
 * reader queries the ledger itself, so it needed the same rule on its own.
 *
 * The rows are that session's, not a shape invented to fit the fix.
 */

const DAY = "2026-09-23";

// center, side, kind, net, fees, status, entry_fill_status
const ROWS: Array<[number, string, string, number, number, string, string | null]> = [
  [7720, "call", "short_vertical", 2.7, 3.4433, "cancelled", "cancelled"],
  [7720, "put", "fly", 0.25, 21.89, "settled", "filled"],
  [7725, "call", "short_vertical", 2.65, 3.4433, "cancelled", "cancelled"],
  [7730, "call", "fly", 0.25, 6.89, "settled", "filled"],
  [7715, "call", "fly", 0.25, 6.89, "settled", "filled"],
  [7705, "call", "fly", 0.25, 16.89, "settled", "filled"],
  [7695, "call", "short_vertical", 2.65, 13.44, "settled", "filled"],
];

let config: ConsoleConfig;

function seed(dir: string, file: string, rows: typeof ROWS): void {
  fs.mkdirSync(dir, { recursive: true });
  const db = new Database(path.join(dir, file));
  db.exec(`
    CREATE TABLE fly_positions (
      position_id TEXT, trade_date TEXT, arm TEXT, symbol TEXT, kind TEXT, side TEXT, center REAL,
      wing_width REAL, far_width REAL, net REAL, quantity INTEGER, fees REAL, status TEXT,
      entry_fill_status TEXT, void_reason TEXT, entry_time TEXT
    );
    CREATE TABLE fly_books (trade_date TEXT, settlement_price REAL, settlement_source TEXT);
    CREATE TABLE fly_iterations (trade_date TEXT, underlying_price REAL, iteration_ts TEXT);
  `);
  const insert = db.prepare(
    `INSERT INTO fly_positions VALUES (?, ?, 'control', 'SPX', ?, ?, ?, 5, NULL, ?, 1, ?, ?, ?, NULL, ?)`,
  );
  rows.forEach(([center, side, kind, net, fees, status, efs], i) =>
    insert.run(`p${i}`, DAY, kind, side, center, net, fees, status, efs, `${DAY}T10:${10 + i}:00`),
  );
  db.prepare("INSERT INTO fly_books VALUES (?, 7706.05, 'yahoo')").run(DAY);
  db.close();
}

beforeEach(() => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "console-flies-cancelled-"));
  const fliesDir = path.join(tmp, "flies");
  seed(fliesDir, "live_trades.db", ROWS);
  config = { paths: { fliesDir } } as unknown as ConsoleConfig;
});

describe("the flies forest", () => {
  it("draws the five positions the broker held, not the seven rows the loop attempted", () => {
    const forest = readFliesForest(config, "live", DAY, "control");
    const curve = forest.arms[0]!.curve;

    expect(curve.positions).toBe(5);
    expect(curve.structures.map((s) => s.center).sort()).toEqual([7695, 7705, 7715, 7720, 7730]);
  });

  it("shows one stranded vertical, not three", () => {
    const curve = readFliesForest(config, "live", DAY, "control").arms[0]!.curve;
    const stranded = curve.structures.filter((s) => s.stranded).map((s) => s.center);

    expect(stranded).toEqual([7695]);
  });

  it("keeps a paper row whose fill status was never recorded", () => {
    // The paper loop has never written entry_fill_status. Unknown is not cancelled -- a filter
    // written as "entry_fill_status = 'filled'" would empty the paper forest entirely.
    const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "console-flies-paper-"));
    const fliesDir = path.join(tmp, "flies");
    seed(fliesDir, "paper_trades.db", [[7705, "call", "fly", 0.25, 6.89, "settled", null]]);
    const paper = { paths: { fliesDir } } as unknown as ConsoleConfig;

    expect(readFliesForest(paper, "paper", DAY, "control").arms[0]!.curve.positions).toBe(1);
  });
});

describe("the forest's profitable windows", () => {
  /**
   * With the cancelled entries gone, 2026-09-23's real book is profitable in four separate
   * windows, not one. The caption used to name only the window around the price and call
   * everything else a loss. The floor already listed every window; what it did not say was which
   * ends of the outermost windows are real edges and which are just where the scan stopped.
   */
  it("reports every window, and which outer ends are open", () => {
    const floor = readFliesForest(config, "live", DAY, "control").arms[0]!.curve.floor;

    expect(floor.bands.length).toBe(4);
    expect(floor.bandsOpen.below).toBe(true); // below 7695 every leg expires worthless: pays forever
    expect(floor.bandsOpen.above).toBe(false); // above the 7730 fly the stranded vertical loses
    expect(floor.bands[1]![0]).toBeLessThanOrEqual(7706.05); // settlement sits inside a real window
    expect(floor.bands[1]![1]).toBeGreaterThanOrEqual(7706.05);
  });
});
