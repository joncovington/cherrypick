import { beforeAll, describe, expect, it } from "vitest";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import Database from "better-sqlite3";
import type { ConsoleConfig } from "../src/config.js";
import { readPmccHistory } from "../src/readers/pmcc.js";
import { readPmccSummary } from "../src/readers/summary/pmcc.js";

/**
 * pmcc's simplified-view summary on a fixture ledger built from pmcc's own schema
 * (`fixtures/pmcc-ledger-schema.sql`). A trade is a closed POSITION booked to its close session,
 * its money the history reader's own; a position whose delivered shares await cover is open; the
 * shorts, rolls and exposure flags are bounded by the same window as the money.
 */

const SCHEMA = fs.readFileSync(path.join(__dirname, "fixtures", "pmcc-ledger-schema.sql"), "utf-8");
const DAY = "2026-10-14"; // a Wednesday; its week is 10-12..10-18
let config: ConsoleConfig;
let missing: ConsoleConfig;

function seed(db: Database.Database): void {
  const pos = db.prepare(
    `INSERT INTO pmcc_positions (position_id, symbol, arm, entry_session, closed_session, quantity,
       long_expiration, long_strike, short_expiration, short_strike, net_debit, entry_long_dte, status,
       exit_reason, gross_pnl, fees, entry_slippage, exit_slippage, settlement_fees)
     VALUES (?, ?, ?, ?, ?, 1, '2027-09-17', 100, '2026-10-16', 110, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
  );
  // control XSP, closed on DAY: fees total 20 = 9 trading + 2 settlement + 9 slippage; net 280.
  pos.run("p1", "XSP", "control", "2026-10-06", DAY, 20, 21, "closed", "short_expiration", 300, 20, 5, 4, 2);
  // control QQQ, closed 10-13, settlement split never recorded: n/r, stays inside fees. Net -115.
  pos.run("p2", "QQQ", "control", "2026-10-06", "2026-10-13", 25, 21, "closed", "tv_exhausted", -100, 15, 3, 3, null);
  // shield SLV, stopped on DAY, no long-leg row (long cost not recorded). Net -60.
  pos.run("p3", "SLV", "shield", "2026-10-06", DAY, 18, 360, "closed", "stop_loss", -50, 10, 2, 2, 0);
  // control XSP, a month earlier. Net 72.
  pos.run("p4", "XSP", "control", "2026-09-01", "2026-09-10", 20, 21, "closed", "short_expiration", 80, 8, 1, 1, 0);
  // open shield GLD: money stays out of the hero.
  pos.run("p5", "GLD", "shield", "2026-10-06", null, 9.2, 360, "open", null, null, 30, 2, null, null);
  // short_settled shield_hold IWM: listed in history, not summed; open here.
  pos.run("p6", "IWM", "shield_hold", "2026-10-06", null, 30, 360, "short_settled", null, null, 12, 1, null, null);

  const leg = db.prepare(
    `INSERT INTO pmcc_legs (position_id, leg_role, occ_symbol, streamer_symbol, expiration, strike, option_type,
       action, entry_mid, status, close_kind, close_value, opened_session)
     VALUES (?, ?, 'X', 'X', '2027-09-17', 100, 'C', ?, ?, ?, ?, ?, ?)`,
  );
  leg.run("p1", "long_call", "Buy to Open", 25, "closed", "traded", 26, "2026-10-06");
  leg.run("p1", "short_call_1", "Sell to Open", 5, "closed", "traded", 1, "2026-10-06");
  leg.run("p2", "long_call", "Buy to Open", 30, "closed", "traded", 29, "2026-10-06");
  leg.run("p4", "short_call_1", "Sell to Open", 2, "closed", "traded", 1, null); // undated short
  leg.run("p5", "long_call", "Buy to Open", 10, "open", null, null, "2026-10-06");
  leg.run("p5", "short_call_1", "Sell to Open", 1.0, "closed", "rolled", 0.5, "2026-10-06");
  leg.run("p5", "short_call_2", "Sell to Open", 0.8, "open", null, null, "2026-10-13");
  leg.run("p6", "short_call_1", "Sell to Open", 1.0, "settled", null, null, "2026-10-12");

  const ev = db.prepare(
    `INSERT INTO pmcc_management_events (position_id, occurred_at, session_date, action, reason, executed)
     VALUES (?, 0, ?, ?, 'expiry', ?)`,
  );
  ev.run("p5", "2026-10-13", "roll_short", 1);
  ev.run("p5", DAY, "roll_short", 0); // not executed
  ev.run("p5", "2026-10-02", "roll_short", 1); // outside the week

  const mark = db.prepare(
    `INSERT INTO pmcc_marks (position_id, leg_role, marked_at, session_date, mid, short_tv, assignment_exposed, usable)
     VALUES (?, ?, ?, ?, ?, ?, ?, ?)`,
  );
  mark.run("p5", "short_call_2", 1, "2026-10-13", 0.7, 0.02, 1, 1); // exposed
  mark.run("p5", "short_call_2", 2, DAY, 0.6, 0.5, 0, 1);
  mark.run("p5", "short_call_2", 3, DAY, null, 0.01, 1, 0); // refused: not counted
  mark.run("p5", "short_call_1", 0, "2026-10-01", 0.9, 0.01, 1, 1); // outside the week
  mark.run("p5", "long_call", 2, DAY, 11, null, 0, 1);
}

beforeAll(() => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "console-summary-pmcc-"));
  const dir = path.join(tmp, "pmcc");
  const cfgDir = path.join(tmp, "config");
  fs.mkdirSync(dir);
  fs.mkdirSync(cfgDir);
  fs.writeFileSync(
    path.join(cfgDir, "pmcc.json"),
    JSON.stringify({ books: { control: { enabled: false }, shield: {}, shield_hold: {} } }),
  );
  const db = new Database(path.join(dir, "paper_trades.db"));
  db.exec(SCHEMA);
  seed(db);
  db.close();
  const paths = { pmccDir: dir, fliesConfig: path.join(cfgDir, "flies.json") };
  config = { paths } as unknown as ConsoleConfig;
  missing = { paths: { ...paths, pmccDir: path.join(tmp, "nowhere") } } as unknown as ConsoleConfig;
});

const summary = (range: string | undefined, day: string | null, mode: "paper" | "live" = "paper") =>
  readPmccSummary(config, { mode, range, day });

describe("pmcc summary money", () => {
  it("equals the history reader's totals for the same window", () => {
    const s = summary("week", DAY);
    const h = readPmccHistory(config, { arm: null, symbol: null, range: { from: "2026-10-12", to: "2026-10-18" } }).totals;
    expect(s.hero.trades).toBe(h.positions);
    expect(s.hero.net).toBe(h.net);
    expect(s.hero.gross).toBe(h.gross);
    expect(s.hero.costs.find((c) => c.label === "fees")?.amount).toBe(h.fees);
    expect(s.hero.costs.find((c) => c.label === "slippage (charged)")?.amount).toBe(h.slippage);
    expect(s.hero.net).toBe(105); // 280 - 115 - 60
  });

  it("reads settlement n/r when a row never recorded its split, and every line adds up", () => {
    const s = summary("week", DAY);
    const cost = (l: string) => s.hero.costs.find((c) => c.label === l)?.amount;
    expect(cost("settlement")).toBeNull();
    expect(cost("fees")).toBe(9 + 9 + 6); // p1 9, p2 9 (settlement inside), p3 6
    expect(cost("slippage (charged)")).toBe(9 + 6 + 4);
    const day = summary("day", DAY);
    expect(day.hero.costs.find((c) => c.label === "settlement")?.amount).toBe(2);
    const lines = day.hero.costs.reduce((t, c) => t + (c.amount ?? 0), 0);
    expect(day.hero.gross - lines).toBeCloseTo(day.hero.net, 2);
  });

  it("keeps open and awaiting-cover positions out of the money, and counts them", () => {
    const s = summary("all", null);
    expect(s.hero.trades).toBe(4);
    expect(s.hero.open).toBe(2);
    const mtm = s.tiles.find((t) => t.label === "open now, marked to market");
    // p5: long +1.00, rolled short +0.50, open short +0.20 = 170 gross, 30 fees; p6 unpriced.
    expect(mtm?.value).toBe(140);
    expect(mtm?.note).toContain("1/2");
  });

  it("is paper whatever mode is asked for", () => {
    expect(summary("week", DAY, "live").mode).toBe("paper");
    expect(summary("week", DAY, "live").hero.net).toBe(105);
  });
});

describe("pmcc summary windows", () => {
  it("resolves no day to the latest close session, with its neighbours", () => {
    const s = summary(undefined, null);
    expect(s.session).toEqual({ day: DAY, prev: "2026-10-13", next: null });
    expect(s.window).toMatchObject({ from: DAY, to: DAY });
    expect(s.hero.trades).toBe(2);
  });

  it("follows the page's day for week and month", () => {
    expect(summary("week", DAY).window).toMatchObject({ from: "2026-10-12", to: "2026-10-18" });
    const m = summary("month", "2026-09-10");
    expect(m.window).toMatchObject({ from: "2026-09-01", to: "2026-09-30" });
    expect(m.hero.trades).toBe(1);
    expect(m.hero.net).toBe(72);
    expect(m.session).toEqual({ day: "2026-09-10", prev: null, next: "2026-10-13" });
  });

  it("bounds shorts sold, rolls and exposure flags by the same window", () => {
    const s = summary("week", DAY);
    const tile = (l: string) => s.tiles.find((t) => t.label === l);
    expect(tile("short calls sold")?.value).toBe(2); // p5's short_call_2, p6's short
    expect(tile("short calls sold")?.note).toContain("1 older short legs");
    expect(tile("shorts rolled")?.value).toBe(1);
    expect(tile("positions flagged assignment-exposed")?.value).toBe(1);
    const g = s.gauges.find((x) => x.title === "marks flagged assignment-exposed");
    expect(g?.rawPct).toBe(50); // 1 of 2 usable short marks; the refused one is not a mark
  });

  it("states return on long cost over the positions that recorded it", () => {
    const s = summary("week", DAY);
    expect(s.hero.basis).toEqual({ label: "long calls bought", amount: 5500 });
    const g = s.gauges.find((x) => x.title === "return on long cost");
    expect(g?.rawPct).toBeCloseTo((165 / 5500) * 100, 6);
    expect(g?.caption).toContain("2/3");
  });
});

describe("pmcc summary arms and breakdowns", () => {
  it("lists every arm in the window, retired flagged, footed to the hero", () => {
    const s = summary("week", DAY);
    expect(s.arms.map((a) => [a.arm, a.trades, a.net, a.retired])).toEqual([
      ["control", 2, 165, true],
      ["shield", 1, -60, false],
    ]);
    expect(s.arms.reduce((t, a) => t + a.net, 0)).toBeCloseTo(s.hero.net, 2);
  });

  it("breaks the window down by symbol, by how it ended and by long held", () => {
    const s = summary("week", DAY);
    const b = (t: string) => s.breakdowns.find((x) => x.title === t)?.rows.map((r) => [r.bucket, r.trades, r.net]);
    expect(b("by symbol")).toEqual([
      ["QQQ", 1, -115],
      ["SLV", 1, -60],
      ["XSP", 1, 280],
    ]);
    expect(b("by how it ended")).toEqual([["closed", 3, 105]]);
    expect(b("by long held")).toEqual([
      ["held-long", 1, -60],
      ["weekly", 2, 165],
    ]);
  });
});

describe("pmcc summary with no ledger", () => {
  it("returns an empty slice with a note, never throws", () => {
    const s = readPmccSummary(missing, { mode: "paper", range: "week", day: null });
    expect(s.hero.trades).toBe(0);
    expect(s.arms).toEqual([]);
    expect(s.notes.join(" ")).toContain("could not be read");
  });
});
