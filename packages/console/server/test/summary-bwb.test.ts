import { beforeAll, describe, expect, it } from "vitest";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import Database from "better-sqlite3";
import type { ConsoleConfig } from "../src/config.js";
import { readBwbHistory } from "../src/readers/bwb.js";
import { readBwbSummary } from "../src/readers/summary/bwb.js";

/**
 * bwb's simplified-view summary against a fixture ledger with the module's real column names.
 * The hero must be the history reader's totals for the same window, to the cent.
 */

let config: ConsoleConfig;
let emptyConfig: ConsoleConfig;

beforeAll(() => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "console-bwb-summary-"));
  fs.mkdirSync(path.join(tmp, "bwb"));
  fs.mkdirSync(path.join(tmp, "config"));
  // `delta` retired in the module's own registry (the `books` spelling).
  fs.writeFileSync(
    path.join(tmp, "config", "bwb.json"),
    JSON.stringify({ books: { control: { enabled: true }, delta: { enabled: false }, flip: { enabled: true } } }),
  );
  const db = new Database(path.join(tmp, "bwb", "paper_trades.db"));
  db.exec(`
    CREATE TABLE bwb_positions (
      id INTEGER PRIMARY KEY AUTOINCREMENT, position_id TEXT, symbol TEXT, arm TEXT,
      entry_session TEXT, closed_session TEXT, status TEXT, exit_reason TEXT, body_strike REAL,
      near_strike REAL, far_strike REAL, expiration TEXT, entry_spot REAL, entry_credit REAL,
      entry_narrow_width REAL, entry_wide_width REAL, entry_max_loss REAL, entry_dte INTEGER,
      armed_at TEXT, addon_fired_at TEXT, addon_credit REAL, gross_pnl REAL, fees REAL,
      quantity INTEGER, itm_settlements INTEGER, entry_cost REAL, entry_slippage REAL,
      addon_cost REAL, addon_slippage REAL, settlement_fees REAL, fees_source TEXT,
      addon_short_strike REAL, addon_long_strike REAL
    );
  `);
  const ins = db.prepare(
    `INSERT INTO bwb_positions (position_id, symbol, arm, entry_session, closed_session, status,
       exit_reason, entry_credit, entry_narrow_width, entry_wide_width, entry_dte, addon_fired_at,
       addon_credit, gross_pnl, fees, quantity, itm_settlements, entry_slippage, addon_slippage,
       settlement_fees)
     VALUES (?, 'SPX', ?, ?, ?, 'closed', ?, ?, 5, 10, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?)`,
  );
  // Week of 2026-10-12 (Mon) .. 10-18. Two closes on Fri 10-16, one on Wed 10-14.
  // Settled ITM, add-on fired: gross -300, fees total 17.74 (slip 1.20 + 0.80, settlement 10).
  ins.run("a", "delta", "2026-10-09", "2026-10-16", "expired", 0.85, 7, "2026-10-13T11:00", 0.4, -300, 17.74, 2, 1.2, 0.8, 10);
  // Expired worthless, control.
  ins.run("b", "control", "2026-10-09", "2026-10-16", "expired", 0.85, 7, null, null, 85, 4.64, 0, 1.2, null, 0);
  // Closed early, settlement split never recorded -> settlement line n/r for any window holding it.
  ins.run("c", "flip", "2026-10-12", "2026-10-14", "closed", 0.8, 4, null, null, 40, 9.64, 0, 1.2, null, null);
  // Previous week (10-09 close), outside the week window, inside the month.
  ins.run("d", "control", "2026-10-02", "2026-10-09", "expired", 0.9, 7, null, null, 90, 4.64, 0, 1.2, null, 0);
  // Open and cancelled rows: no money.
  db.prepare(
    `INSERT INTO bwb_positions (position_id, symbol, arm, entry_session, status, entry_credit, quantity, fees)
     VALUES ('o1', 'SPX', 'control', '2026-10-15', 'open', 0.85, 1, 4.64),
            ('o2', 'SPX', 'flip', '2026-10-15', 'armed', 0.85, 1, 4.64),
            ('x1', 'SPX', 'control', '2026-10-15', 'cancelled', 0.85, 1, 0)`,
  ).run();
  db.close();
  config = { paths: { bwbDir: path.join(tmp, "bwb"), fliesConfig: path.join(tmp, "config", "flies.json") } } as unknown as ConsoleConfig;
  emptyConfig = {
    paths: { bwbDir: path.join(tmp, "nope"), fliesConfig: path.join(tmp, "config", "flies.json") },
  } as unknown as ConsoleConfig;
});

const historyTotals = (from: string | null, to: string | null) =>
  readBwbHistory(config, { arm: null, symbol: null, range: { from, to } }, { limit: 500, offset: 0 }).totals;

describe("bwb summary", () => {
  it("resolves day=null to the latest close session, with neighbours", () => {
    const s = readBwbSummary(config, { mode: "paper", range: "day", day: null });
    expect(s.session).toEqual({ day: "2026-10-16", prev: "2026-10-14", next: null });
    expect(s.window).toMatchObject({ from: "2026-10-16", to: "2026-10-16" });
    expect(s.hero.trades).toBe(2);
  });

  it("hero equals the history reader's totals for the same window", () => {
    for (const [range, from, to] of [
      ["day", "2026-10-16", "2026-10-16"],
      ["week", "2026-10-12", "2026-10-18"],
      ["month", "2026-10-01", "2026-10-31"],
    ] as const) {
      const s = readBwbSummary(config, { mode: "paper", range, day: "2026-10-16" });
      const t = historyTotals(from, to)!;
      expect(s.hero.trades).toBe(t.positions);
      expect(s.hero.net).toBe(t.net);
      expect(s.hero.gross).toBe(t.gross);
      expect(s.hero.costs.find((c) => c.label === "fees")?.amount).toBe(t.fees);
      expect(s.hero.costs.find((c) => c.label === "slippage (charged)")?.amount).toBe(t.slippage);
    }
  });

  it("pins the day's money to the cent", () => {
    const s = readBwbSummary(config, { mode: "paper", range: "day", day: "2026-10-16" });
    // a: -300 - 17.74 = -317.74; b: 85 - 4.64 = 80.36.
    expect(s.hero.net).toBe(-237.38);
    expect(s.hero.costs).toEqual([
      { label: "fees", amount: 9.18 },
      expect.objectContaining({ label: "settlement", amount: 10 }),
      { label: "slippage (charged)", amount: 3.2 },
    ]);
    expect(s.hero.basis?.amount).toBe(210); // 85 + 40 + 85
  });

  it("reads settlement n/r when a row in the window never recorded it", () => {
    const s = readBwbSummary(config, { mode: "paper", range: "week", day: "2026-10-16" });
    expect(s.hero.costs.find((c) => c.label === "settlement")?.amount).toBeNull();
  });

  it("follows the day for week and month windows", () => {
    const week = readBwbSummary(config, { mode: "paper", range: "week", day: "2026-10-14" });
    expect(week.window).toMatchObject({ from: "2026-10-12", to: "2026-10-18" });
    expect(week.hero.trades).toBe(3);
    const prevWeek = readBwbSummary(config, { mode: "paper", range: "week", day: "2026-10-09" });
    expect(prevWeek.hero.trades).toBe(1);
    expect(prevWeek.session).toEqual({ day: "2026-10-09", prev: null, next: "2026-10-14" });
    const month = readBwbSummary(config, { mode: "paper", range: "month", day: "2026-10-09" });
    expect(month.hero.trades).toBe(4);
  });

  it("keeps open rows out of the money and counts them; cancelled is neither", () => {
    const s = readBwbSummary(config, { mode: "paper", range: "week", day: "2026-10-16" });
    expect(s.hero.open).toBe(2);
    expect(s.hero.trades).toBe(3);
  });

  it("lists every arm in the window, retired flagged, footed to the hero", () => {
    const s = readBwbSummary(config, { mode: "paper", range: "week", day: "2026-10-16" });
    expect(s.arms.map((a) => a.arm).sort()).toEqual(["control", "delta", "flip"]);
    expect(s.arms.find((a) => a.arm === "delta")?.retired).toBe(true);
    expect(s.arms.find((a) => a.arm === "control")?.retired).toBe(false);
    expect(Math.round(s.arms.reduce((x, a) => x + a.net, 0) * 100)).toBe(Math.round(s.hero.net * 100));
  });

  it("computes the module's KPIs from the same rows", () => {
    const s = readBwbSummary(config, { mode: "paper", range: "day", day: "2026-10-16" });
    const tile = (l: string) => s.tiles.find((t) => t.label === l)?.value;
    expect(tile("add-on fired")).toBe(1);
    expect(tile("settled ITM")).toBe(1);
    expect(tile("avg entry credit")).toBe(0.85);
    expect(tile("add-on credit")).toBe(40);
    // fees 9.18 / premium 210.
    expect(tile("fee drag")).toBe(4.37);
    // worst case per share max(0, 10 - 5 - 0.85) = 4.15 -> $415 each, $830; net -237.38.
    expect(s.gauges[0]?.rawPct).toBeCloseTo((-237.38 / 830) * 100, 6);
    const exit = s.breakdowns[0]!.rows;
    expect(exit.map((r) => r.bucket)).toEqual(["expired worthless", "settled ITM"]);
    expect(s.breakdowns[2]!.rows).toEqual([expect.objectContaining({ bucket: "7 DTE", trades: 2 })]);
    expect(s.breakdowns[1]!.rows).toEqual([expect.objectContaining({ bucket: "Fri", trades: 2 })]);
  });

  it("returns an empty slice with a note, never throws, when the ledger is missing", () => {
    const s = readBwbSummary(emptyConfig, { mode: "live", range: "week", day: null });
    expect(s.hero.trades).toBe(0);
    expect(s.arms).toEqual([]);
    expect(s.notes.length).toBe(1);
    expect(s.mode).toBe("live");
  });
});
