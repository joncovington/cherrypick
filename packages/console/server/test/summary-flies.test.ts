import { describe, it, expect, beforeAll } from "vitest";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import Database from "better-sqlite3";
import type { ConsoleConfig } from "../src/config.js";
import { readFliesSummary } from "../src/readers/summary/flies.js";
import { readFliesTradeLog, NO_TRADE_LOG_QUERY } from "../src/readers/flies.js";

/**
 * The flies simplified-view summary, pinned on a fixture ledger: the hero is the trade log's own
 * totals for the same window, settlement reads n/r when a row never split it, windows follow the
 * page's session, open/cancelled/voided rows carry no money, every arm is listed with the retired
 * one flagged, the era bound keeps the XSP books out, and a missing ledger reads empty.
 */

let config: ConsoleConfig;
let emptyConfig: ConsoleConfig;
const DAY = "2026-10-14"; // a Wednesday; its week is 10-12..10-18
const PREV = "2026-10-13";

const DDL = `
  CREATE TABLE fly_positions (
    id INTEGER PRIMARY KEY, position_id TEXT, book_id TEXT, trade_date TEXT, entry_time TEXT,
    symbol TEXT, arm TEXT, entry_mode TEXT, kind TEXT, side TEXT, center REAL, wing_width REAL,
    far_width REAL, entry_window TEXT, quantity INTEGER, net REAL, gross_pnl REAL, fees REAL,
    settlement_fees REAL, slippage_dollars REAL, expiry_payoff REAL, closed_before_expiry INTEGER,
    floor_dollars REAL, risk_free INTEGER, pnl REAL, completion_latency_min REAL, pinned INTEGER,
    status TEXT, void_reason TEXT, completed_at TEXT
  );`;

type Row = [
  id: string, date: string, time: string, symbol: string, arm: string, mode: string, kind: string,
  qty: number, net: number, gross: number | null, fees: number | null, settle: number | null,
  floor: number | null, pnl: number | null, status: string, voidReason: string | null,
];

function seed(file: string, rows: Row[]): void {
  const db = new Database(file);
  db.exec(DDL);
  const ins = db.prepare(
    `INSERT INTO fly_positions (position_id, trade_date, entry_time, symbol, arm, entry_mode, kind,
       quantity, net, gross_pnl, fees, settlement_fees, slippage_dollars, floor_dollars, pnl, status,
       void_reason, pinned)
     VALUES (?, ?, ? , ?, ?, ?, ?, ?, ?, ?, ?, ?, 0.5, ?, ?, ?, ?, 0)`,
  );
  for (const r of rows) ins.run(r[0], r[1], `${r[1]}T${r[2]}:00-04:00`, ...r.slice(3));
  db.close();
}

const PAPER: Row[] = [
  // completed legged fly: 0.25 cr, fees 21.89 of which 15 settlement, floor 3.11
  ["p1", DAY, "10:05", "SPX", "control", "legged", "fly", 1, 0.25, 25, 21.89, 15, 3.11, 3.11, "settled", null],
  // stranded vertical
  ["p2", DAY, "11:20", "SPX", "control", "legged", "short_vertical", 1, 2.5, -250, 13.44, 10, -268.44, -263.44, "settled", null],
  // debit-first completion: a debit entry collects no premium
  ["p4", DAY, "12:00", "SPX", "control", "debit_first", "fly", 1, -0.4, 100, 10, 5, 50, 90, "settled", null],
  // still open, cancelled, voided: none of them carries money
  ["p5", DAY, "13:30", "SPX", "control", "legged", "short_vertical", 1, 2.4, null, 3.44, null, -270, null, "open", null],
  ["p6", DAY, "13:40", "SPX", "control", "legged", "short_vertical", 1, 2.4, null, 3.44, null, -270, null, "cancelled", null],
  ["p7", DAY, "14:00", "SPX", "control", "legged", "fly", 1, 2.0, 500, 5, 0, 10, 495, "settled", "duplicate"],
  // previous session, a retired arm, settlement split never recorded, 2 lots
  ["p3", PREV, "13:10", "SPX", "old", "legged", "fly", 2, 0.3, 60, 20, null, 40, 40, "settled", null],
  // same era, earlier month
  ["p8", "2026-09-01", "10:30", "SPX", "control", "legged", "fly", 1, 0.2, 20, 10, 5, 10, 10, "settled", null],
  // the XSP era: never pooled
  ["p9", "2026-07-30", "10:30", "XSP", "control", "legged", "fly", 1, 0.2, 1000, 0, 0, 10, 1000, "settled", null],
];

const LIVE: Row[] = [
  ["l1", "2026-10-15", "11:00", "SPX", "control", "legged", "fly", 1, 0.25, 25, 6.89, 0, 3.11, 18.11, "settled", null],
];

function cfg(tmp: string): ConsoleConfig {
  return {
    port: 0,
    paths: {
      cherrypick: tmp,
      streamCacheDb: path.join(tmp, "stream_cache.db"),
      watchdogLast: path.join(tmp, "watchdog.last.json"),
      orchestratorConfig: path.join(tmp, "config.json"),
      consoleData: path.join(tmp, "console"),
      meicDir: path.join(tmp, "meic"),
      fliesDir: path.join(tmp, "flies"),
      earningsDir: path.join(tmp, "earnings"),
      gexDir: path.join(tmp, "gex"),
      reviewDir: path.join(tmp, "review"),
      manageDir: path.join(tmp, "manage"),
      overviewDir: path.join(tmp, "overview"),
      technicalsDir: path.join(tmp, "technicals"),
      advisorDir: path.join(tmp, "advisor"),
      adviceDir: path.join(tmp, "state", "advice"),
      meicRiskConfig: path.join(tmp, "config.risk.json"),
      fliesConfig: path.join(tmp, "config", "flies.json"),
    },
  } as ConsoleConfig;
}

beforeAll(() => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "console-summary-flies-"));
  fs.mkdirSync(path.join(tmp, "flies"), { recursive: true });
  seed(path.join(tmp, "flies", "paper_trades.db"), PAPER);
  seed(path.join(tmp, "flies", "live_trades.db"), LIVE);
  fs.mkdirSync(path.join(tmp, "config"), { recursive: true });
  fs.writeFileSync(
    path.join(tmp, "config", "flies.json"),
    JSON.stringify({ arms: { control: { enabled: true }, old: { enabled: false } } }),
  );
  config = cfg(tmp);
  emptyConfig = cfg(fs.mkdtempSync(path.join(os.tmpdir(), "console-summary-flies-empty-")));
});

const summary = (range: string | undefined, day: string | null, mode: "paper" | "live" = "paper", scope: { arm?: string | null; symbol?: string | null; era?: string | null } = {}) =>
  readFliesSummary(config, { mode, range, day, ...scope });
const log = (from: string, to: string) => readFliesTradeLog(config, "paper", { ...NO_TRADE_LOG_QUERY, from, to }).totals;
const cost = (s: ReturnType<typeof summary>, label: string) => s.hero.costs.find((c) => c.label === label)?.amount;

describe("the flies summary's money", () => {
  it("equals the trade log's totals for the same window", () => {
    const s = summary("week", DAY);
    const t = log("2026-10-12", "2026-10-18");
    expect(s.hero.trades).toBe(t.trades);
    expect(s.hero.sessions).toBe(t.sessions);
    expect(s.hero.net).toBeCloseTo(t.netPnl, 2);
    expect(s.hero.gross).toBeCloseTo(t.grossPnl, 2);
    expect(cost(s, "fees")).toBeCloseTo(t.fees, 2);
    expect(s.hero.net).toBeCloseTo(3.11 - 263.44 + 90 + 40, 2);
  });

  it("adds up: gross − fees − settlement = net on a fully recorded window", () => {
    const s = summary("day", DAY);
    expect(s.hero.trades).toBe(3);
    expect(cost(s, "settlement")).toBe(30);
    expect(cost(s, "fees")).toBeCloseTo(6.89 + 3.44 + 5, 2);
    expect(s.hero.gross - (cost(s, "fees") ?? 0) - (cost(s, "settlement") ?? 0)).toBeCloseTo(s.hero.net, 2);
  });

  it("reads settlement n/r when any row never split it, and keeps that row's total in fees", () => {
    const s = summary("week", DAY);
    expect(cost(s, "settlement")).toBeNull();
    expect(cost(s, "fees")).toBeCloseTo(6.89 + 3.44 + 5 + 20, 2);
  });

  it("charges no slippage line: the modelled fill already concedes it", () => {
    expect(summary("week", DAY).hero.costs.map((c) => c.label)).toEqual(["fees", "settlement"]);
  });

  it("keeps open, cancelled and voided rows out of the money, and counts the open one", () => {
    const s = summary("day", DAY);
    expect(s.hero.open).toBe(1);
    expect(s.hero.net).toBeCloseTo(3.11 - 263.44 + 90, 2);
  });
});

describe("the flies summary's window", () => {
  it("resolves no day to the latest settled session, with its neighbour", () => {
    const s = summary(undefined, null);
    expect(s.session).toEqual({ day: DAY, prev: PREV, next: null });
    expect(s.window).toEqual({ from: DAY, to: DAY, label: "day" });
  });

  it("widens week and month from the page's session", () => {
    expect(summary("week", DAY).window.from).toBe("2026-10-12");
    expect(summary("week", DAY).hero.trades).toBe(4);
    expect(summary("month", "2026-09-01").hero.trades).toBe(1);
    expect(summary("month", DAY).hero.trades).toBe(4);
  });

  it("keeps the XSP era out of 'all'", () => {
    const s = summary("all", null);
    expect(s.hero.trades).toBe(5);
    expect(s.hero.net).toBeCloseTo(3.11 - 263.44 + 90 + 40 + 10, 2);
  });

  it("honours the live ledger", () => {
    const s = summary("all", null, "live");
    expect(s.mode).toBe("live");
    expect(s.hero.trades).toBe(1);
    expect(s.hero.net).toBe(18.11);
    expect(s.session.day).toBe("2026-10-15");
  });
});

describe("the flies summary's KPIs", () => {
  it("rates completion over settled legged entries only", () => {
    const g = summary("week", DAY).gauges[0]!;
    expect(g.rawPct).toBeCloseTo((2 / 3) * 100, 6);
    expect(g.caption).toBe("2 of 3 settled legged entries became a fly");
  });

  it("splits completed flies from stranded verticals and sums the locked floor", () => {
    const tiles = Object.fromEntries(summary("week", DAY).tiles.map((t) => [t.label, t.value]));
    expect(tiles["completed flies net"]).toBeCloseTo(3.11 + 90 + 40, 2);
    expect(tiles["stranded verticals net"]).toBeCloseTo(-263.44, 2);
    expect(tiles["floor locked by completions"]).toBeCloseTo(3.11 + 50 + 40, 2);
  });

  it("states fee drag as trading fees over premium collected", () => {
    const s = summary("week", DAY);
    const premium = 25 + 250 + 60; // the debit-first entry collected nothing
    expect(s.hero.basis).toEqual({ label: "premium collected", amount: premium });
    const drag = s.tiles.find((t) => t.label === "fee drag")!;
    expect(drag.value).toBeCloseTo(((6.89 + 3.44 + 5 + 20) / premium) * 100, 6);
  });

  it("breaks the window down so each table foots to the hero", () => {
    const s = summary("week", DAY);
    for (const b of s.breakdowns) {
      expect(b.rows.reduce((n, r) => n + r.net, 0)).toBeCloseTo(s.hero.net, 2);
    }
    expect(s.breakdowns[0]!.rows.map((r) => r.bucket)).toEqual(["completed fly", "stranded vertical"]);
    expect(s.breakdowns[1]!.rows.map((r) => r.bucket)).toEqual(["10:00-11:00", "11:00-12:00", "12:00-13:00", "13:00-14:00"]);
    expect(s.breakdowns[2]!.rows.map((r) => r.bucket)).toEqual(["Tue", "Wed"]);
  });

  it("lists every arm in the window with the retired one flagged", () => {
    const arms = summary("week", DAY).arms;
    expect(arms.map((a) => [a.arm, a.retired])).toEqual([
      ["old", true],
      ["control", false],
    ]);
  });

  it("notes the completion-rule break only when the window spans it", () => {
    const spans = (s: ReturnType<typeof summary>) => s.notes.some((n) => n.includes("2026-10-05"));
    expect(spans(summary("month", DAY))).toBe(true);
    expect(spans(summary("week", DAY))).toBe(false);
  });
});

describe("the flies summary's scope", () => {
  it("narrows the hero and the by-arm table to the header's arm", () => {
    const all = summary("week", DAY);
    const scoped = summary("week", DAY, "paper", { arm: "control" });
    expect(all.hero.trades).toBe(4);
    expect(scoped.hero.trades).toBe(3);
    expect(scoped.hero.net).toBeCloseTo(3.11 - 263.44 + 90, 2);
    expect(scoped.arms.map((a) => a.arm)).toEqual(["control"]);
  });

  it("filters by symbol only where the header names one", () => {
    expect(summary("all", null, "paper", { symbol: "SPX" }).hero.trades).toBe(5);
    expect(summary("all", null, "paper", { era: "ALL", symbol: "XSP" }).hero.trades).toBe(1);
  });

  it("pools every era only when the header widens to ALL", () => {
    expect(summary("all", null).hero.trades).toBe(5);
    expect(summary("all", null, "paper", { era: "ALL" }).hero.trades).toBe(6);
  });

  it("scopes the open count and the day arrows, not just the settled roll-up", () => {
    const all = summary("all", null);
    expect(all.hero.open).toBe(1); // p5: still open, under control
    expect(all.session.day).toBe(DAY);
    const old = summary("all", null, "paper", { arm: "old" });
    expect(old.hero.open).toBe(0); // the retired arm has no open position
    expect(old.session.day).toBe(PREV); // the arrows step through the arm's own settled sessions
  });
});

describe("a missing flies ledger", () => {
  it("reads as an empty slice with a note, never a throw", () => {
    const s = readFliesSummary(emptyConfig, { mode: "paper", range: "week", day: null });
    expect(s.hero.trades).toBe(0);
    expect(s.arms).toEqual([]);
    expect(s.notes.length).toBeGreaterThan(0);
  });
});
