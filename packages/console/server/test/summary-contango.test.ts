import { afterAll, beforeAll, describe, expect, it } from "vitest";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import Database from "better-sqlite3";
import type { ConsoleConfig } from "../src/config.js";
import { readContangoHistory } from "../src/readers/contango.js";
import { closePooledDbs } from "../src/readers/db.js";
import { readContangoSummary } from "../src/readers/summary/contango.js";

/**
 * Contango's simplified-view summary, on a fixture ledger built from the module's own schema
 * (`fixtures/contango-ledger-schema.sql`). A trade is a stint, booked to the session it was sold;
 * the hero must equal the history reader's totals for the same window, slippage is charged, a
 * share trade has no settlement (n/r), and open stints carry no money.
 */

const SCHEMA = fs.readFileSync(path.join(__dirname, "fixtures", "contango-ledger-schema.sql"), "utf-8");
const DAY = "2026-10-14"; // a Wednesday; its week is 10-12..10-18
let config: ConsoleConfig;
let tmp: string;

beforeAll(() => {
  tmp = fs.mkdtempSync(path.join(os.tmpdir(), "console-contango-summary-"));
  const dir = path.join(tmp, "contango");
  fs.mkdirSync(dir);
  fs.mkdirSync(path.join(tmp, "config"));
  fs.writeFileSync(
    path.join(tmp, "config", "contango.json"),
    JSON.stringify({ arms: { control: {}, flipexit: { exit_at_or_above: 1.0 }, oldarm: { enabled: false } } }),
  );
  const db = new Database(path.join(dir, "paper_trades.db"));
  db.exec(SCHEMA);
  const ins = db.prepare(
    `INSERT INTO contango_positions (position_id, arm, symbol, role, shares, entry_session, entry_mid, entry_value,
       entry_fees, entry_slippage, status, exit_session, exit_mid, exit_value, exit_fees, exit_slippage, exit_reason,
       distributions, gross_pnl, fees, slippage, net_pnl)
     VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
  );
  // p1, sold on DAY: -9950 + 10149 = 199 gross; 199 - 0.06 - 4.02 = 194.92 net.
  ins.run("control:SVXY:2026-10-12", "control", "SVXY", "risk", 199, "2026-10-12", 50.0, -9950.0, 0, 1.99, "closed",
    DAY, 51.0, 10149.0, 0.06, 2.03, "switch_to_cash", 0, 199.0, 0.06, 4.02, 194.92);
  // p2, same week, with a distribution: -10046.4 + 10050 + 12.5 = 16.1 gross; 12.03 net.
  ins.run("flipexit:SHV:2026-09-28", "flipexit", "SHV", "cash", 91, "2026-09-28", 110.4, -10046.4, 0, 2.01, "closed",
    "2026-10-13", 110.44, 10050.0, 0.05, 2.01, "switch_to_risk", 12.5, 16.1, 0.05, 4.02, 12.03);
  // p3, same week, on a retired arm, slippage never recorded: -100 gross, -100.03 net.
  ins.run("oldarm:SVXY:2026-10-09", "oldarm", "SVXY", "risk", 100, "2026-10-09", 50.0, -5000.0, 0, null, "closed",
    "2026-10-13", 49.0, 4900.0, 0.03, null, "switch_to_cash", 0, -100.0, 0.03, null, -100.03);
  // p4, a month earlier: -10000 + 10010 + 5 = 15 gross; 15 - 0.05 - 2 = 12.95 net.
  ins.run("control:SHV:2026-09-01", "control", "SHV", "cash", 90, "2026-09-01", 111.11, -10000.0, 0, 1.0, "closed",
    "2026-09-15", 111.22, 10010.0, 0.05, 1.0, "switch_to_risk", 5, 15.0, 0.05, 2.0, 12.95);
  // Open stints: no money anywhere in the summary.
  ins.run("control:SHV:2026-10-14", "control", "SHV", "cash", 91, DAY, 110.4, -10046.4, 0, 2.01, "open",
    null, null, null, null, null, null, 0, null, null, null, null);
  ins.run("flipexit:SVXY:2026-10-13", "flipexit", "SVXY", "risk", 200, "2026-10-13", 50.0, -10000.0, 0, 2.0, "open",
    null, null, null, null, null, null, 0, null, null, null, null);
  const s = db.prepare(
    "INSERT INTO contango_sessions (trade_date, arm, action, state_after, holding_symbol) VALUES (?, ?, ?, ?, ?)",
  );
  s.run("2026-10-13", "flipexit", "switch", "risk", "SVXY");
  s.run("2026-10-13", "oldarm", "switch", "cash", "SHV");
  s.run(DAY, "control", "switch", "cash", "SHV");
  s.run(DAY, "flipexit", "hold", "risk", "SVXY");
  s.run(DAY, "oldarm", "missed", "cash", "SHV");
  s.run("2026-09-15", "control", "switch", "risk", "SVXY");
  db.close();
  config = {
    paths: {
      contangoDir: dir,
      contangoConfigCandidates: [],
      fliesConfig: path.join(tmp, "config", "flies.json"),
    },
  } as unknown as ConsoleConfig;
});

afterAll(() => closePooledDbs());

const summary = (range: string | undefined, day: string | null) =>
  readContangoSummary(config, { mode: "live", range, day });
const historyTotals = (from: string | null, to: string | null) =>
  readContangoHistory(config, { arm: null, range: { from, to } }).totals;

describe("contango summary money", () => {
  it("equals the history reader's totals for the same window", () => {
    for (const [range, from, to] of [
      ["day", DAY, DAY],
      ["week", "2026-10-12", "2026-10-18"],
      ["month", "2026-10-01", "2026-10-31"],
      ["all", null, null],
    ] as const) {
      const s = summary(range, DAY);
      const t = historyTotals(from, to);
      expect(s.hero.net).toBe(t.net);
      expect(s.hero.gross).toBe(t.gross);
      expect(s.hero.trades).toBe(t.positions);
    }
    expect(summary("day", DAY).hero.net).toBe(194.92);
    expect(summary("week", DAY).hero.net).toBe(106.92);
  });

  it("charges slippage as its own line, n/r when a stint did not record it, and settlement always n/r", () => {
    const day = summary("day", DAY).hero;
    expect(day.costs.map((c) => c.label)).toEqual(["fees", "settlement", "slippage (charged)"]);
    expect(day.costs[0]!.amount).toBe(0.06);
    expect(day.costs[1]!.amount).toBeNull();
    expect(day.costs[2]!.amount).toBe(4.02);
    expect(day.gross - 0.06 - 4.02).toBeCloseTo(day.net, 2);
    const week = summary("week", DAY).hero;
    expect(week.costs[2]!.amount).toBeNull();
  });

  it("states cash deployed as the basis and the return on it", () => {
    const s = summary("day", DAY);
    expect(s.hero.basis).toEqual({ label: "cash deployed", amount: 9950 });
    expect(s.gauges[0]!.rawPct).toBeCloseTo((194.92 / 9950) * 100, 6);
  });

  it("keeps open stints out of the money and counts those bought by the window's end", () => {
    const day = summary("day", DAY);
    expect(day.hero.open).toBe(2);
    expect(day.hero.trades).toBe(1);
    expect(summary("month", "2026-09-15").hero.open).toBe(0);
  });

  it("follows the page's session for the window and resolves a null day to the latest sale", () => {
    const latest = summary("day", null);
    expect(latest.session).toEqual({ day: DAY, prev: "2026-10-13", next: null });
    const sept = summary("month", "2026-09-15");
    expect(sept.window).toMatchObject({ from: "2026-09-01", to: "2026-09-30" });
    expect(sept.hero.net).toBe(12.95);
    expect(sept.session).toEqual({ day: "2026-09-15", prev: null, next: "2026-10-13" });
  });

  it("reads sessions held, switches, missed windows and distributions from the window", () => {
    const tile = (s: ReturnType<typeof summary>, label: string) => s.tiles.find((t) => t.label === label)?.value;
    const day = summary("day", DAY);
    expect(day.tiles.slice(0, 2).map((t) => t.label)).toEqual(["sessions in SVXY", "sessions in SHV"]);
    expect(tile(day, "sessions in SVXY")).toBe(1);
    expect(tile(day, "sessions in SHV")).toBe(2);
    expect(tile(day, "switches")).toBe(1);
    expect(tile(day, "missed windows")).toBe(1);
    const week = summary("week", DAY);
    expect(tile(week, "switches")).toBe(3);
    expect(tile(week, "distributions received")).toBe(12.5);
    // slippage over (purchase + sale) on the two stints that recorded it
    expect(tile(week, "slippage, % of traded value")).toBeCloseTo((8.04 / (9950 + 10149 + 10046.4 + 10050)) * 100, 4);
  });

  it("breaks down by fund, by how it ended and by stint length", () => {
    const [byFund, byExit, byLength] = summary("week", DAY).breakdowns;
    expect(byFund!.rows.map((r) => [r.bucket, r.trades, r.net])).toEqual([
      ["SHV", 1, 12.03],
      ["SVXY", 2, 94.89],
    ]);
    expect(byExit!.rows.map((r) => r.bucket)).toEqual(["switched to cash", "switched to risk"]);
    expect(byLength!.rows.map((r) => [r.bucket, r.trades])).toEqual([
      ["2–5 days", 2],
      ["6–20 days", 1],
    ]);
  });

  it("lists every arm in the window with the retired one flagged, footing to the hero", () => {
    const s = summary("week", DAY);
    expect(s.arms.map((a) => [a.arm, a.retired])).toEqual([
      ["control", false],
      ["flipexit", false],
      ["oldarm", true],
    ]);
    expect(s.arms.reduce((t, a) => t + a.net, 0)).toBeCloseTo(s.hero.net, 2);
  });

  it("is paper only whatever mode is asked for", () => {
    expect(summary("day", DAY).mode).toBe("paper");
  });

  it("returns an empty slice with a note, never throwing, when the ledger is missing", () => {
    const missing = {
      paths: { ...config.paths, contangoDir: path.join(tmp, "nowhere") },
    } as unknown as ConsoleConfig;
    const s = readContangoSummary(missing, { mode: "paper", range: "week", day: DAY });
    expect(s.hero.trades).toBe(0);
    expect(s.arms).toEqual([]);
    expect(s.notes.length).toBe(1);
  });
});
