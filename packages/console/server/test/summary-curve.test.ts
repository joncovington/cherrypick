import { afterAll, beforeAll, describe, expect, it } from "vitest";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import Database from "better-sqlite3";
import { loadConfig, type ConsoleConfig } from "../src/config.js";
import { readCurveHistory } from "../src/readers/curve.js";
import { closePooledDbs } from "../src/readers/db.js";
import { readCurveSummary } from "../src/readers/summary/curve.js";

/**
 * curve's simplified-view summary, pinned on a fixture ledger built from the module's own DDL
 * (`packages/curve/src/cherrypick/curve/db.py`): the hero equals readCurveHistory's totals for the
 * same window, settlement reads n/r where a row never split it, windows follow the page's session,
 * open spreads carry no money, every arm is listed with the retired one flagged, and a missing
 * ledger reads as an empty slice rather than a throw.
 */

const DAY = "2026-10-14"; // a Wednesday; its week is 10-12..10-18

const DDL = `
CREATE TABLE curve_positions (
  id INTEGER PRIMARY KEY AUTOINCREMENT, position_id TEXT NOT NULL UNIQUE, symbol TEXT NOT NULL,
  arm TEXT NOT NULL, entry_session TEXT NOT NULL, quantity INTEGER NOT NULL DEFAULT 1,
  expiration TEXT NOT NULL, short_strike REAL NOT NULL, long_strike REAL NOT NULL, entry_time TEXT,
  entry_spot REAL, entry_short_mid REAL, entry_long_mid REAL, entry_credit REAL, entry_width REAL,
  entry_max_loss REAL, entry_credit_pct_of_width REAL, entry_short_delta REAL, short_selected_by TEXT,
  entry_dte INTEGER, entry_ratio REAL, entry_regime TEXT, entry_hook INTEGER, entry_cost REAL,
  entry_slippage REAL, advice_params TEXT, experiment_id TEXT, exposure_ticks INTEGER,
  status TEXT NOT NULL DEFAULT 'open', exit_reason TEXT, closed_at TEXT, closed_session TEXT,
  exit_value REAL, exit_cost REAL, exit_slippage REAL, settlement_spot REAL, itm_settlements INTEGER,
  gross_pnl REAL, fees REAL, settlement_fees REAL, created_at TEXT, updated_at TEXT
);
CREATE TABLE curve_marks (
  id INTEGER PRIMARY KEY AUTOINCREMENT, position_id TEXT NOT NULL, leg_role TEXT, marked_at REAL NOT NULL,
  session_date TEXT NOT NULL, bid REAL, ask REAL, mid REAL, delta REAL, iv REAL, spot REAL,
  close_cost REAL, short_tv REAL, assignment_exposed INTEGER, quote_age_s REAL,
  usable INTEGER NOT NULL DEFAULT 0, refusal TEXT
);
CREATE TABLE curve_regime (
  id INTEGER PRIMARY KEY AUTOINCREMENT, trade_date TEXT NOT NULL UNIQUE, tick TEXT NOT NULL,
  recorded_at TEXT, ratio REAL, regime TEXT, hook INTEGER, vix REAL, vix3m REAL, vix_age_s REAL,
  vix3m_age_s REAL, usable INTEGER NOT NULL DEFAULT 0, refusal TEXT, created_at TEXT, updated_at TEXT
);
CREATE TABLE curve_assignments (
  id INTEGER PRIMARY KEY AUTOINCREMENT, position_id TEXT NOT NULL, leg_role TEXT NOT NULL,
  symbol TEXT NOT NULL, assigned_session TEXT NOT NULL, assigned_at TEXT, direction TEXT NOT NULL,
  shares INTEGER NOT NULL, basis REAL NOT NULL, strike REAL NOT NULL, option_type TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'open', disposed_session TEXT, disposed_at TEXT, disposal_price REAL,
  share_pnl REAL, fees REAL, created_at TEXT, updated_at TEXT, UNIQUE(position_id, leg_role)
);
CREATE TABLE measurement_breaks (
  id INTEGER PRIMARY KEY AUTOINCREMENT, break_date TEXT NOT NULL, key TEXT NOT NULL, old_value TEXT,
  new_value TEXT, note TEXT, recorded_at REAL, UNIQUE(break_date, key)
);`;

let tmp: string;
let config: ConsoleConfig;

beforeAll(() => {
  tmp = fs.mkdtempSync(path.join(os.tmpdir(), "console-summary-curve-"));
  const dir = path.join(tmp, "curve");
  fs.mkdirSync(dir, { recursive: true });
  fs.mkdirSync(path.join(tmp, "config"), { recursive: true });
  fs.writeFileSync(
    path.join(tmp, "config", "curve.json"),
    JSON.stringify({ arms: { control: { enabled: true }, noflip: {}, hook: { enabled: false }, near: {} } }),
  );

  const db = new Database(path.join(dir, "paper_trades.db"));
  db.exec(DDL);
  const pos = db.prepare(
    `INSERT INTO curve_positions (position_id, symbol, arm, entry_session, quantity, expiration, short_strike,
       long_strike, entry_credit, entry_width, entry_max_loss, entry_credit_pct_of_width, entry_regime, status,
       exit_reason, closed_session, gross_pnl, fees, entry_slippage, exit_slippage, settlement_fees)
     VALUES (?, 'VXX', ?, ?, ?, '2026-11-20', 40, 42, ?, 2, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
  );
  // p1 control: closed on DAY, every cost split recorded. net 50 - 14 = 36; fees line 14 - 7 - 0 = 7.
  pos.run("p1", "control", "2026-10-08", 2, 0.4, 1.6, 0.2, "contango", "closed", "profit_take", DAY, 50, 14, 4, 3, 0);
  // p3 hook (retired): closed on DAY by a flip. net -120 - 10 = -130; fees line 10 - 4 - 1 = 5.
  pos.run("p3", "hook", "2026-10-07", 1, 0.4, 1.6, 0.2, "backwardation", "closed", "regime_flip", DAY, -120, 10, 2, 2, 1);
  // p2 noflip: same week, settlement split never recorded (n/r on the week).
  pos.run("p2", "noflip", "2026-10-08", 2, 0.4, 1.6, 0.2, "contango", "closed", "profit_take", "2026-10-13", 50, 14, 4, 3, null);
  // p4 control: same month, entered before the 10-06 break, expired with shares delivered.
  pos.run("p4", "control", "2026-10-01", 1, 0.2, 0.8, 0.2, "contango", "closed", "expired", "2026-10-09", -80, 28, 1, 1, 6);
  // p5 near: still open -- no money anywhere.
  pos.run("p5", "near", "2026-10-13", 2, 0.5, 1.5, 0.25, "contango", "open", null, null, null, 3, 2, null, null);
  // p6 control: a month earlier.
  pos.run("p6", "control", "2026-09-10", 1, 0.3, 0.7, 0.3, "contango", "closed", "close_dte", "2026-09-15", 20, 6, 1, 1, 0);

  db.prepare(
    `INSERT INTO curve_assignments (position_id, leg_role, symbol, assigned_session, direction, shares, basis, strike, option_type)
     VALUES ('p4', 'short_call', 'VXX', '2026-10-09', 'short', -100, 41, 40, 'C')`,
  ).run();
  const mark = db.prepare(
    "INSERT INTO curve_marks (position_id, marked_at, session_date, short_tv, assignment_exposed, usable) VALUES (?, ?, ?, ?, ?, ?)",
  );
  mark.run("p1", 1, "2026-10-10", 0.01, 1, 1); // exposed
  mark.run("p3", 2, "2026-10-10", 0.2, 0, 1); // not exposed
  mark.run("p3", 3, "2026-10-11", 0.01, 1, 0); // exposed but unusable: does not count
  const regime = db.prepare("INSERT INTO curve_regime (trade_date, tick, regime, usable) VALUES (?, 't', ?, ?)");
  regime.run("2026-10-12", "contango", 1);
  regime.run("2026-10-13", "contango", 1);
  regime.run("2026-10-14", "backwardation", 1);
  regime.run("2026-10-15", null, 0);
  db.prepare("INSERT INTO measurement_breaks (break_date, key) VALUES ('2026-10-06', 'defaults.spread_width')").run();
  db.close();

  config = loadConfig();
  config.paths.curveDir = dir;
  config.paths.fliesConfig = path.join(tmp, "config", "flies.json");
});

afterAll(() => closePooledDbs());

const q = (range: string | undefined, day: string | null) => readCurveSummary(config, { mode: "live", range, day });
const cost = (s: ReturnType<typeof q>, label: string) => s.hero.costs.find((c) => c.label === label)?.amount;

describe("curve's summary money", () => {
  it("equals readCurveHistory's totals for the same window", () => {
    for (const range of ["day", "week", "month", "year", "all"]) {
      const s = q(range, DAY);
      const h = readCurveHistory(config, { arm: null, symbol: null, range: { from: s.window.from, to: s.window.to } });
      expect(s.hero.net).toBe(h.totals.net);
      expect(s.hero.gross).toBe(h.totals.gross);
      expect(s.hero.trades).toBe(h.totals.positions);
    }
  });

  it("splits every cost line once, and reads settlement n/r where a row never split it", () => {
    const day = q("day", DAY);
    expect(day.mode).toBe("paper"); // paper-only: the requested mode is never honoured
    expect(day.hero.net).toBe(-94);
    expect(day.hero.gross).toBe(-70);
    expect(cost(day, "fees")).toBe(12);
    expect(cost(day, "settlement")).toBe(1);
    expect(cost(day, "slippage (charged)")).toBe(11);
    expect(day.hero.basis).toEqual({ label: "premium collected", amount: 120 }); // 0.40 x 100 x 2 + 0.40 x 100
    const week = q("week", DAY);
    expect(cost(week, "settlement")).toBeNull();
    expect(week.hero.net).toBe(-58);
  });

  it("widens week and month from the page's session, and resolves day = null to the latest close", () => {
    expect(q("week", DAY).window).toMatchObject({ from: "2026-10-12", to: "2026-10-18" });
    expect(q("month", DAY).hero.trades).toBe(4);
    expect(q("week", "2026-09-15").hero.trades).toBe(1);
    const latest = q(undefined, null);
    expect(latest.session).toEqual({ day: DAY, prev: "2026-10-13", next: null });
    expect(latest.hero.trades).toBe(2);
  });

  it("keeps the open spread out of the money and counts it as open", () => {
    const week = q("week", DAY);
    expect(week.hero.open).toBe(1);
    expect(week.arms.find((a) => a.arm === "near")).toBeUndefined();
    expect(q("week", "2026-09-15").hero.open).toBe(0); // entered after that window ended
  });

  it("lists every arm in the window, footed to the hero, with the retired arm flagged", () => {
    const week = q("week", DAY);
    expect(week.arms.map((a) => a.arm).sort()).toEqual(["control", "hook", "noflip"]);
    expect(week.arms.find((a) => a.arm === "hook")!.retired).toBe(true);
    expect(week.arms.find((a) => a.arm === "control")!.retired).toBe(false);
    expect(week.arms.reduce((s, a) => s + a.net, 0)).toBeCloseTo(week.hero.net, 2);
  });

  it("reads return on max loss, drag, exposure and the regime series in the module's terms", () => {
    const day = q("day", DAY);
    expect(day.gauges[0]!.rawPct).toBeCloseTo((-94 / 480) * 100, 6); // 1.60 x 100 x 2 + 1.60 x 100
    const tile = (s: ReturnType<typeof q>, label: string) => s.tiles.find((t) => t.label === label)!;
    expect(tile(day, "fee drag").value).toBeCloseTo((12 / 120) * 100, 6);
    expect(tile(day, "cost drag").value).toBeCloseTo((24 / 120) * 100, 6);
    expect(tile(day, "credit as % of width").value).toBeCloseTo(20, 6);
    expect(tile(day, "assignment-exposed trades").value).toBe(1); // p1 only; p3's exposed mark was unusable
    expect(tile(q("month", DAY), "ended with shares delivered").value).toBe(1);
    const week = q("week", DAY);
    expect(tile(week, "regime days recorded").value).toBe(3);
    expect(tile(week, "regime days recorded").note).toContain("of 4 written; 2 contango, 1 backwardation");
    expect(tile(week, "sessions with an entry").value).toBe(1); // p5 on 10-13
    const byRegime = day.breakdowns.find((b) => b.title === "by regime at entry")!;
    expect(byRegime.rows.map((r) => [r.bucket, r.net])).toEqual([
      ["backwardation", -130],
      ["contango", 36],
    ]);
    const byWeekday = q("week", DAY).breakdowns.find((b) => b.title === "by entry weekday")!;
    expect(byWeekday.rows.map((r) => [r.bucket, r.trades])).toEqual([
      ["Wed", 1],
      ["Thu", 2],
    ]);
  });

  it("says when a window straddles a measurement break, and only then", () => {
    expect(q("month", DAY).notes.some((n) => n.includes("2026-10-06"))).toBe(true);
    expect(q("week", DAY).notes.some((n) => n.includes("2026-10-06"))).toBe(false);
    expect(q("day", DAY).notes.some((n) => n.includes("upper bound"))).toBe(true);
  });

  it("returns an empty slice with a note, never a throw, when the ledger is missing", () => {
    const gone = { ...config, paths: { ...config.paths, curveDir: path.join(tmp, "nowhere") } };
    const s = readCurveSummary(gone, { mode: "paper", range: "week", day: DAY });
    expect(s.hero.trades).toBe(0);
    expect(s.arms).toEqual([]);
    expect(s.notes.length).toBe(1);
  });
});
