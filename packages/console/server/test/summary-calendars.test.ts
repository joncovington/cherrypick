import { beforeAll, describe, expect, it } from "vitest";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import Database from "better-sqlite3";
import type { ConsoleConfig } from "../src/config.js";
import { readCalendarsWeeks } from "../src/readers/calendars.js";
import { readCalendarsSummary } from "../src/readers/summary/calendars.js";
import type { SummaryQuery } from "../src/readers/summary/common.js";

/**
 * Calendars' simplified-view summary, on a fixture ledger built from calendars' own schema
 * (`fixtures/calendars-ledger-schema.sql`). The money must be the history table's
 * (`readCalendarsWeeks`) for the same window, to the cent; a trade is one (week, arm) double
 * calendar, booked to its Monday; holiday structures keep their own rows.
 */

const SCHEMA = fs.readFileSync(path.join(__dirname, "fixtures", "calendars-ledger-schema.sql"), "utf-8");
let config: ConsoleConfig;
let missing: ConsoleConfig;

const q = (range: string | undefined, day: string | null): SummaryQuery => ({ mode: "paper", range, day });

beforeAll(() => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "console-summary-calendars-"));
  const dir = path.join(tmp, "calendars");
  fs.mkdirSync(dir);
  const db = new Database(path.join(dir, "paper_trades.db"));
  db.exec(SCHEMA);
  const ins = db.prepare(
    `INSERT INTO dc_positions (position_id, week_of, entry_session, arm, side, symbol, structure,
       front_expiration, back_expiration, strike, quantity, entry_debit, status, exit_reason,
       gross_pnl, fees, entry_slippage, exit_slippage, settlement_fees, itm_settlements)
     VALUES (?, ?, ?, ?, ?, 'SPY', ?, '2026-01-01', '2026-01-02', 600, 1, ?, ?, ?, ?, ?, 0.5, 0.5, ?, ?)`,
  );
  // Week 09-28, control: net 9.87 (settlement 5 recorded on the ITM put).
  ins.run("a:c:call", "2026-09-28", "2026-09-28", "control", "call", "dc_4_7", 2.0, "closed", "scheduled_exit", 30, 4.0, 0, 0);
  ins.run("a:c:put", "2026-09-28", "2026-09-28", "control", "put", "dc_4_7", 1.8, "closed", "scheduled_exit", -10, 6.13, 5.0, 1);
  // Week 09-28, path: settlement split NEVER recorded, and shares delivered on the put. net -2.
  ins.run("a:p:call", "2026-09-28", "2026-09-28", "path", "call", "dc_4_7", 2.0, "closed", "expired", 50, 3.0, null, 0);
  ins.run("a:p:put", "2026-09-28", "2026-09-28", "path", "put", "dc_4_7", 1.8, "closed", "long_disposition", -40, 9.0, null, 1);
  db.prepare(
    `INSERT INTO dc_assignments (position_id, leg_role, symbol, assigned_session, direction, shares, basis,
       strike, option_type, status) VALUES ('a:p:put', 'short', 'SPY', '2026-10-02', 'long', 100, 598, 600, 'put', 'disposed')`,
  ).run();
  // Labor Day week 09-07, a Tuesday entry: dc_3_6, net 16.
  ins.run("h:c:call", "2026-09-07", "2026-09-08", "control", "call", "dc_3_6", 2.0, "closed", "scheduled_exit", 20, 2.0, 0, 0);
  ins.run("h:c:put", "2026-09-07", "2026-09-08", "control", "put", "dc_3_6", 1.0, "closed", "scheduled_exit", 0, 2.0, 0, 0);
  // Week 10-05: control net -84, friday:control (dc_7_10) net 46.
  ins.run("b:c:call", "2026-10-05", "2026-10-05", "control", "call", "dc_4_7", 2.0, "closed", "scheduled_exit", -100, 2.0, 0, 0);
  ins.run("b:c:put", "2026-10-05", "2026-10-05", "control", "put", "dc_4_7", 2.0, "closed", "scheduled_exit", 20, 2.0, 0, 0);
  ins.run("b:f:call", "2026-10-05", "2026-10-02", "friday:control", "call", "dc_7_10", 1.5, "closed", "scheduled_exit", 40, 2.0, 0, 0);
  ins.run("b:f:put", "2026-10-05", "2026-10-02", "friday:control", "put", "dc_7_10", 1.5, "closed", "scheduled_exit", 10, 2.0, 0, 0);
  // Week 10-12: control half closed, half open -- an unfinished week, no money anywhere.
  ins.run("c:c:call", "2026-10-12", "2026-10-12", "control", "call", "dc_4_7", 2.0, "closed", "scheduled_exit", 5, 2.0, 0, 0);
  ins.run("c:c:put", "2026-10-12", "2026-10-12", "control", "put", "dc_4_7", 2.0, "open", null, null, 1.0, null, 0);
  // Entry attempts: an ex-dividend week and a stale-cache week, both untraded; a traded week.
  const att = db.prepare("INSERT INTO dc_entry_attempts (ts, trade_date, week_of, symbol, outcome) VALUES ('t', ?, ?, 'SPY', ?)");
  att.run("2026-09-14", "2026-09-14", "ex_dividend_week");
  att.run("2026-09-14", "2026-09-14", "ex_dividend_week");
  att.run("2026-08-17", "2026-08-17", "no_fresh_quotes");
  att.run("2026-10-05", "2026-10-05", "filled");
  db.close();

  const cfgFile = path.join(tmp, "calendars.json");
  fs.writeFileSync(
    cfgFile,
    JSON.stringify({
      settlement_style: { SPY: "physical" },
      books: { control: { enabled: true }, path: { enabled: false } },
      friday_entry: { enabled: true },
    }),
  );
  config = { paths: { calendarsDir: dir, calendarsConfigCandidates: [cfgFile] } } as unknown as ConsoleConfig;
  missing = {
    paths: { calendarsDir: path.join(tmp, "nowhere"), calendarsConfigCandidates: [] },
  } as unknown as ConsoleConfig;
});

describe("calendars summary money", () => {
  it("equals the history table's totals for the same window, to the cent", () => {
    for (const [range, day] of [
      ["month", "2026-10-07"],
      ["month", "2026-09-30"],
      ["week", "2026-09-30"],
      ["all", null],
      ["year", "2026-10-07"],
    ] as const) {
      const s = readCalendarsSummary(config, q(range, day));
      const t = readCalendarsWeeks(config, { from: s.window.from, to: s.window.to }).totals;
      expect(s.hero.net, `${range} ${day}`).toBe(t.net);
      expect(s.hero.gross).toBe(t.gross);
      expect(s.hero.trades).toBe(t.positions);
      const net = s.hero.gross - s.hero.costs.reduce((a, c) => a + (c.amount ?? 0), 0);
      if (s.hero.costs.every((c) => c.amount !== null)) expect(net).toBeCloseTo(s.hero.net, 2);
    }
    expect(readCalendarsSummary(config, q("all", null)).hero.net).toBe(-14.13); // 9.87 - 2 + 16 - 84 + 46
  });

  it("charges slippage as its own cost line", () => {
    const s = readCalendarsSummary(config, q("month", "2026-10-07"));
    expect(s.hero.costs.map((c) => c.label)).toEqual(["fees", "settlement", "slippage (charged)"]);
    // two weeks x two positions x (0.5 + 0.5)
    expect(s.hero.costs.find((c) => c.label === "slippage (charged)")?.amount).toBe(4);
  });

  it("reads settlement n/r, not zero, when a row never recorded it", () => {
    const sep = readCalendarsSummary(config, q("month", "2026-09-30"));
    expect(sep.hero.costs.find((c) => c.label === "settlement")?.amount).toBeNull();
    const oct = readCalendarsSummary(config, q("month", "2026-10-07"));
    expect(oct.hero.costs.find((c) => c.label === "settlement")?.amount).toBe(0);
  });

  it("books a week to its Monday, and the week window follows the day", () => {
    const w = readCalendarsSummary(config, q("week", "2026-10-08"));
    expect(w.window).toMatchObject({ from: "2026-10-05", to: "2026-10-11" });
    expect(w.hero.net).toBe(-38);
    expect(w.session).toEqual({ day: "2026-10-05", prev: "2026-09-28", next: null });
    // a mid-week day on the "day" range snaps to its Monday rather than reading empty
    expect(readCalendarsSummary(config, q("day", "2026-09-30")).hero.net).toBe(7.87);
  });

  it("resolves no day to the latest week with a finished trade", () => {
    const s = readCalendarsSummary(config, q("day", null));
    expect(s.session.day).toBe("2026-10-05");
    expect(s.hero.trades).toBe(2);
  });

  it("keeps open weeks out of the money and counts them", () => {
    const s = readCalendarsSummary(config, q("week", "2026-10-14"));
    expect(s.hero.trades).toBe(0);
    expect(s.hero.net).toBe(0);
    expect(s.hero.gross).toBe(0);
    expect(s.hero.open).toBe(1);
    expect(s.gauges[0]?.rawPct).toBeNull();
  });

  it("states return on the debit paid", () => {
    const s = readCalendarsSummary(config, q("week", "2026-10-05"));
    // control 400 + friday:control 300 paid
    expect(s.hero.basis).toEqual({ label: "debit paid", amount: 700 });
    expect(s.gauges[0]?.rawPct).toBeCloseTo((-38 / 700) * 100, 6);
  });
});

describe("calendars summary KPIs", () => {
  it("lists every arm in the window, retired flagged", () => {
    const arms = readCalendarsSummary(config, q("all", null)).arms;
    expect(arms.map((a) => a.arm).sort()).toEqual(["control", "friday:control", "path"]);
    expect(arms.find((a) => a.arm === "path")?.retired).toBe(true);
    expect(arms.find((a) => a.arm === "control")?.retired).toBe(false);
    expect(arms.reduce((a, r) => a + r.net, 0)).toBeCloseTo(readCalendarsSummary(config, q("all", null)).hero.net, 2);
  });

  it("never pools a holiday structure with a normal week", () => {
    const b = readCalendarsSummary(config, q("all", null)).breakdowns.find((x) => x.title === "by week type");
    expect(b?.rows.map((r) => [r.bucket.split(" ")[0], r.trades])).toEqual([
      ["dc_4_7", 3],
      ["dc_7_10", 1],
      ["dc_3_6", 1],
    ]);
  });

  it("names how each week ended and its settlement style", () => {
    const s = readCalendarsSummary(config, q("all", null));
    const ended = s.breakdowns.find((x) => x.title === "by how it ended");
    expect(ended?.rows.find((r) => r.bucket === "assigned")?.trades).toBe(1);
    expect(ended?.rows.find((r) => r.bucket === "settled")?.trades).toBe(1);
    const u = s.breakdowns.find((x) => x.title === "by underlying");
    expect(u?.rows.map((r) => r.bucket)).toEqual(["SPY (physical)"]);
  });

  it("counts weeks traded and skipped, ex-dividend named", () => {
    const tiles = readCalendarsSummary(config, q("all", null)).tiles;
    expect(tiles.find((t) => t.label === "weeks traded")?.value).toBe(4);
    const skipped = tiles.find((t) => t.label === "weeks skipped");
    expect(skipped?.value).toBe(2);
    expect(skipped?.note).toContain("1 ex-dividend");
    expect(skipped?.note).toContain("no_fresh_quotes");
  });

  it("returns an empty slice with a note when the ledger is missing", () => {
    const s = readCalendarsSummary(missing, q("week", "2026-10-07"));
    expect(s.hero.trades).toBe(0);
    expect(s.arms).toEqual([]);
    expect(s.notes.length).toBeGreaterThan(0);
  });
});
