import { afterAll, beforeAll, describe, expect, it } from "vitest";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import Database from "better-sqlite3";
import type { ConsoleConfig } from "../src/config.js";
import { closePooledDbs } from "../src/readers/db.js";
import { readWeeklyputHistory } from "../src/readers/weeklyput.js";
import { readWeeklyputSummary } from "../src/readers/summary/weeklyput.js";
import { SUMMARY_READERS } from "../src/readers/summary/index.js";

/**
 * Weekly put's simplified-view summary, on a ledger built from the module's own schema
 * (`fixtures/weeklyput-ledger-schema.sql`). A trade is one settled put booked to its expiry; the
 * hero must equal the history reader's totals, net = gross - fees - settlement - slippage, and an
 * open put counts but carries no money.
 */

const SCHEMA = fs.readFileSync(path.join(__dirname, "fixtures", "weeklyput-ledger-schema.sql"), "utf-8");
const DAY = "2026-11-06"; // a Friday; its week is 11-02..11-08
let tmp: string;
let config: ConsoleConfig;
let emptyConfig: ConsoleConfig;
let absentConfig: ConsoleConfig;

const COLS = `position_id, arm, symbol, root, settlement_style, expiration, entry_session, strike, quantity, entry_spot,
  entry_forward, entry_mid, entry_delta, entry_value, entry_fees, entry_slippage, notional, status, settle_session,
  settle_price, exit_value, exit_reason, settlement, gross_pnl, fees, slippage, net_pnl, net_bp, assignment_exposed_at`;

function ledger(name: string): { dir: string; db: Database.Database } {
  const dir = path.join(tmp, name);
  fs.mkdirSync(dir);
  const db = new Database(path.join(dir, "paper_trades.db"));
  db.exec(SCHEMA);
  return { dir, db };
}

const cfg = (dir: string) => ({ paths: { weeklyputDir: dir } }) as unknown as ConsoleConfig;

beforeAll(() => {
  tmp = fs.mkdtempSync(path.join(os.tmpdir(), "console-weeklyput-summary-"));
  const { dir, db } = ledger("full");
  const ins = db.prepare(`INSERT INTO weeklyput_positions (${COLS}, streamer_symbol) VALUES (${COLS.split(",").map(() => "?").join(",")}, 'x')`);
  // SPX expired worthless: 184 + 0 = 184 gross; 184 - 0.65 - 0 - 5 = 178.35 net.
  ins.run("unhedged:SPX:2026-11-06", "unhedged", "SPX", "SPXW", "cash", DAY, "2026-10-30", 6350, 1, 6500, 6501,
    1.84, 0.1, 184, 0.65, 5, 650100, "closed", DAY, 6512.3, 0, "expired", 0, 184, 0.65, 5, 178.35, 2.7, null);
  // SPY assigned the same week: 90 - 240 = -150 gross; -150 - 0.65 - 2 - 3 = -155.65 net.
  ins.run("unhedged:SPY:2026-11-06", "unhedged", "SPY", "SPY", "physical", DAY, "2026-10-30", 640, 1, 650,
    650, 0.9, 0.1, 90, 0.65, 3, 65000, "closed", "2026-11-09", 637.6, -240, "assigned", 2, -150, 0.65, 3, -155.65, -23.9,
    "2026-11-05T15:00:00");
  // QQQ a fortnight earlier: 120 gross; 120 - 0.65 - 0 - 2 = 117.35 net.
  ins.run("unhedged:QQQ:2026-10-23", "unhedged", "QQQ", "QQQ", "physical", "2026-10-23", "2026-10-16", 500, 1, 520,
    520, 1.2, 0.1, 120, 0.65, 2, 52000, "closed", "2026-10-23", 530, 0, "expired", 0, 120, 0.65, 2, 117.35, 22.6, null);
  // Open put, expiry still ahead.
  ins.run("unhedged:IWM:2026-11-20", "unhedged", "IWM", "IWM", "physical", "2026-11-20", "2026-11-13", 200, 1, 210,
    210, 0.5, 0.09, 50, 0.65, 1, 21000, "open", null, null, null, null, null, null, null, null, null, null, null);
  db.close();
  config = cfg(dir);
  emptyConfig = cfg(ledger("empty").dir);
  absentConfig = cfg(path.join(tmp, "nowhere"));
});

afterAll(() => closePooledDbs());

const summary = (range: string | undefined, day: string | null, c = config) =>
  readWeeklyputSummary(c, { mode: "paper", range, day });

describe("weeklyput summary", () => {
  it("is registered for /api/summary/weeklyput", () => {
    expect(SUMMARY_READERS["weeklyput"]).toBe(readWeeklyputSummary);
  });

  it("reads an absent store, and one with a schema but no rows, as empty", () => {
    const absent = summary("all", null, absentConfig);
    expect(absent.hero.trades).toBe(0);
    expect(absent.hero.net).toBe(0);
    expect(absent.notes.length).toBeGreaterThan(0);
    const empty = summary("all", null, emptyConfig);
    expect(empty.hero).toMatchObject({ trades: 0, open: 0, net: 0 });
    expect(empty.session).toEqual({ day: null, prev: null, next: null });
    expect(empty.arms).toEqual([]);
  });

  it("nets a settled cycle as gross less fees, settlement and slippage, each its own line", () => {
    const h = summary("day", DAY).hero;
    expect(h.costs.map((c) => c.label)).toEqual(["fees", "settlement", "slippage (charged)"]);
    expect(h.gross).toBe(34);
    expect(h.costs.map((c) => c.amount)).toEqual([1.3, 2, 8]);
    expect(h.net).toBe(22.7);
    expect(h.gross - 1.3 - 2 - 8).toBeCloseTo(h.net, 2);
    expect(h.trades).toBe(2);
    expect(h.wins).toBe(1);
  });

  it("equals the history reader's totals for the same window", () => {
    const totals = readWeeklyputHistory(config, { arm: null, range: { from: null, to: null } }).totals;
    const all = summary("all", DAY).hero;
    expect(all.net).toBe(totals.net);
    expect(all.gross).toBe(totals.gross);
    expect(all.trades).toBe(totals.positions);
    expect(all.net).toBe(140.05);
  });

  it("counts an open put but puts no money on it", () => {
    const all = summary("all", null);
    expect(all.hero.open).toBe(1);
    expect(all.hero.trades).toBe(3);
    // Entered 11-13, after the day's end: not open as of 11-06.
    expect(summary("day", DAY).hero.open).toBe(0);
  });

  it("windows by the page's session and finds its neighbours", () => {
    const latest = summary("day", null);
    expect(latest.session).toEqual({ day: DAY, prev: "2026-10-23", next: null });
    const oct = summary("month", "2026-10-23");
    expect(oct.window).toMatchObject({ from: "2026-10-01", to: "2026-10-31" });
    expect(oct.hero.net).toBe(117.35);
    expect(oct.session).toEqual({ day: "2026-10-23", prev: null, next: DAY });
  });

  it("breaks the window down by name and by how the puts ended, and the arms foot to the hero", () => {
    const s = summary("week", DAY);
    expect(s.breakdowns[0]!.rows.map((r) => r.bucket)).toEqual(["SPX", "SPY"]);
    expect(s.breakdowns[1]!.rows.map((r) => r.bucket)).toEqual(["expired worthless", "assigned"]);
    expect(s.tiles.find((t) => t.label === "assigned")?.value).toBe(1);
    expect(s.arms.reduce((n, a) => n + a.net, 0)).toBeCloseTo(s.hero.net, 2);
  });

  it("narrows to a symbol", () => {
    const s = readWeeklyputSummary(config, { mode: "paper", range: "all", day: null, symbol: "SPY" });
    expect(s.hero.trades).toBe(1);
    expect(s.hero.net).toBe(-155.65);
    expect(s.hero.open).toBe(0);
  });
});
