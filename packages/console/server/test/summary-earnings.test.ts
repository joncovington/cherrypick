import { beforeAll, describe, expect, it } from "vitest";
import { setVerdictCaller } from "../src/services/verdictBridge.js";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import Database from "better-sqlite3";
import type { ConsoleConfig } from "../src/config.js";
import { readEarnings } from "../src/readers/earnings.js";
import { readEarningsSummary } from "../src/readers/summary/earnings.js";

// readEarnings marks flagged rows from the module's verdict subprocess; never spawn it here.
setVerdictCaller(() => ({ ok: false, verdict: null, error: "stubbed" }));

/**
 * Earnings' simplified-view summary on a fixture ledger (earnings' own generated schema): the hero
 * is the history reader's rows summed, a cost a row never recorded reads n/r, windows follow the
 * page's session, open positions carry no money, every arm is listed, live honours its own book,
 * and a missing ledger is an empty slice.
 */

const SCHEMA = fs.readFileSync(path.join(__dirname, "fixtures", "earnings-ledger-schema.sql"), "utf-8");
const DAY = "2026-10-14"; // a Wednesday; its week is 10-12..10-18
const at = (day: string, hourUtc = 18) => Date.parse(`${day}T${String(hourUtc).padStart(2, "0")}:00:00Z`) / 1000;

let tmp: string;
let config: ConsoleConfig;

function seed(db: Database.Database): void {
  const ins = db.prepare(
    `INSERT INTO trades (order_id, strategy, symbol, expiration, entry_credit, exit_debit, pnl, opened_at,
       closed_at, quantity, entry_cost, exit_cost, entry_slippage, exit_slippage, settlement_fees, status,
       exit_reason, capital_at_risk, hold_days, arm)
     VALUES (?, ?, ?, '2026-10-17', ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
  );
  // DAY: iron fly at a profit target. gross 120, costs 3+2 (slip 0.5+0.5, no settlement) -> net 115.
  ins.run("a", "iron_fly", "ZM", 2, 0.8, 120, at("2026-10-13"), at(DAY), 3, 2, 0.5, 0.5, 0, "closed", "profit_target", 500, 1, "strat_test:iron_fly");
  // DAY: iron condor stopped out. gross -200, costs 2.5+2.5 -> net -205.
  ins.run("b", "iron_condor", "AEO", 1.5, 3.5, -200, at("2026-10-13"), at(DAY), 2.5, 2.5, 0.4, 0.4, 0, "closed", "stop_loss", 400, 1, "advised:condor-take-earlier:iron_condor");
  // Same week, settlement split never recorded: gross 40, costs 1.4+1.6 -> net 37.
  ins.run("c", "atm_calendar", "INTU", -1.2, -1.6, 40, at("2026-10-12"), at("2026-10-13"), 1.4, 1.6, 0.3, 0.3, null, "closed", "front_expiry", null, null, "strat_test:atm_calendar");
  // A month earlier, outside the week: gross 60, costs 1+1 -> net 58.
  ins.run("d", "iron_fly", "S", 1, 0.4, 60, at("2026-09-01"), at("2026-09-02"), 1, 1, 0.2, 0.2, 0, "closed", "time_exit", 300, 1, "strat_test:iron_fly");
  // Still open: no money anywhere.
  ins.run("e", "iron_fly", "NVDA", 0.9, null, null, at(DAY), null, 2, null, 0.5, null, null, "open", null, 900, null, "strat_test:iron_fly");
}

beforeAll(() => {
  tmp = fs.mkdtempSync(path.join(os.tmpdir(), "console-earnings-summary-"));
  const dir = path.join(tmp, "earnings");
  fs.mkdirSync(dir);
  const paper = new Database(path.join(dir, "paper_trades.db"));
  paper.exec(SCHEMA);
  seed(paper);
  paper.close();
  // The live ledger's reduced schema: no exit_reason, slippage, settlement or hold_days.
  const live = new Database(path.join(dir, "earnings_trades.db"));
  live.exec(`CREATE TABLE trades (
    order_id TEXT PRIMARY KEY, strategy TEXT, symbol TEXT, expiration TEXT, entry_credit REAL, exit_debit REAL,
    pnl REAL, opened_at REAL, closed_at REAL, arm TEXT NOT NULL DEFAULT 'default', quantity INTEGER,
    capital_at_risk REAL, entry_cost REAL, exit_cost REAL, entry_context TEXT, entry_iv REAL, exit_iv REAL)`);
  live
    .prepare(
      `INSERT INTO trades (order_id, strategy, symbol, expiration, entry_credit, pnl, opened_at, closed_at, quantity,
         capital_at_risk, entry_cost, exit_cost, arm)
       VALUES ('L1', 'iron_fly', 'AMD', '2026-10-17', 2, 90, ?, ?, 1, 300, 2, 2, 'default')`,
    )
    .run(at("2026-10-13"), at(DAY));
  live.close();
  config = {
    paths: {
      earningsDir: dir,
      orchestratorConfig: path.join(tmp, "none.json"),
      fliesConfig: path.join(tmp, "config", "flies.json"),
      meicConfig: path.join(tmp, "config", "meic.json"),
    },
  } as unknown as ConsoleConfig;
});

const sum = (xs: number[]) => Math.round(xs.reduce((s, x) => s + x, 0) * 100) / 100;

describe("earnings summary money", () => {
  it("equals the history reader's totals for the same window", () => {
    for (const range of ["day", "week", "month", "all"]) {
      const s = readEarningsSummary(config, { mode: "paper", range, day: DAY });
      const w = s.window;
      const h = readEarnings(config, undefined, null, { from: w.from, to: w.to });
      const rows = h.trades.rows.filter((r) => r.mode === "paper");
      expect(s.hero.trades).toBe(rows.length);
      expect(s.hero.net).toBe(sum(rows.map((r) => r.net ?? 0)));
      expect(s.hero.gross).toBe(sum(rows.map((r) => r.gross ?? 0)));
    }
    const day = readEarningsSummary(config, { mode: "paper", range: "day", day: DAY });
    expect(day.hero.net).toBe(-90);
    expect(day.hero.gross).toBe(-80);
    expect(day.hero.costs).toEqual([
      { label: "fees", amount: 8.2 },
      { label: "settlement", amount: 0, note: expect.any(String) },
      { label: "slippage (charged)", amount: 1.8 },
    ]);
    // Paper only: the live ledger's same-day trade is not in the paper hero.
    const paperOnly = readEarnings(config, undefined, null, { from: DAY, to: DAY }).totals;
    expect(paperOnly.net).toBe(-90 + 86);
  });

  it("reads settlement n/r when a row never recorded it", () => {
    const s = readEarningsSummary(config, { mode: "paper", range: "week", day: DAY });
    expect(s.hero.trades).toBe(3);
    expect(s.hero.net).toBe(-53);
    expect(s.hero.costs.find((c) => c.label === "settlement")?.amount).toBeNull();
  });

  it("widens the window from the page's session", () => {
    expect(readEarningsSummary(config, { mode: "paper", range: "week", day: DAY }).window).toMatchObject({ from: "2026-10-12", to: "2026-10-18" });
    const month = readEarningsSummary(config, { mode: "paper", range: "month", day: "2026-09-02" });
    expect(month.window).toMatchObject({ from: "2026-09-01", to: "2026-09-30" });
    expect(month.hero.net).toBe(58);
  });

  it("resolves a null day to the latest settled session, with neighbours", () => {
    const s = readEarningsSummary(config, { mode: "paper", range: "day", day: null });
    expect(s.session).toEqual({ day: DAY, prev: "2026-10-13", next: null });
    expect(s.hero.trades).toBe(2);
  });

  it("keeps open positions out of the money, counting them", () => {
    const s = readEarningsSummary(config, { mode: "paper", range: "day", day: DAY });
    expect(s.hero.open).toBe(1);
    expect(s.tiles.find((t) => t.label === "names traded")?.value).toBe(2);
    // An earlier window's end precedes the open position's entry.
    expect(readEarningsSummary(config, { mode: "paper", range: "month", day: "2026-09-02" }).hero.open).toBe(0);
  });

  it("states return on capital at risk with its coverage", () => {
    const s = readEarningsSummary(config, { mode: "paper", range: "week", day: DAY });
    // c recorded no max loss: (115 - 205) / (500 + 400) = -10%.
    expect(s.gauges[0]?.rawPct).toBe(-10);
    expect(s.gauges[0]?.caption).toContain("2/3");
    expect(s.tiles.find((t) => t.label === "average sessions held")?.note).toContain("2/3");
  });

  it("lists every arm in the window, footed to the hero", () => {
    const s = readEarningsSummary(config, { mode: "paper", range: "all", day: DAY });
    expect(s.arms.map((a) => a.arm).sort()).toEqual(["advised:condor-take-earlier:iron_condor", "strat_test:atm_calendar", "strat_test:iron_fly"]);
    expect(sum(s.arms.map((a) => a.net))).toBe(s.hero.net);
    expect(s.arms.every((a) => a.retired === false)).toBe(true);
  });

  it("breaks down by structure, exit and symbol, each adding up", () => {
    const s = readEarningsSummary(config, { mode: "paper", range: "all", day: DAY });
    const [byStrategy, byExit, bySymbol] = s.breakdowns;
    expect(byStrategy?.rows.find((r) => r.bucket === "iron_fly")).toMatchObject({ trades: 2, net: 173 });
    for (const b of s.breakdowns) expect(sum(b.rows.map((r) => r.net))).toBe(s.hero.net);
    expect(byExit?.rows.map((r) => r.bucket).sort()).toEqual(["front_expiry", "profit_target", "stop_loss", "time_exit"]);
    expect(bySymbol?.rows[0]?.bucket).toBe("AEO");
  });

  it("honours live mode against the live ledger, its unrecorded costs n/r", () => {
    const s = readEarningsSummary(config, { mode: "live", range: "day", day: null });
    expect(s.mode).toBe("live");
    expect(s.hero.trades).toBe(1);
    expect(s.hero.net).toBe(86);
    expect(s.hero.costs.find((c) => c.label === "slippage (charged)")?.amount).toBeNull();
    expect(s.breakdowns[1]?.rows[0]?.bucket).toBe("n/r");
  });

  it("returns an empty slice for a missing ledger, without throwing", () => {
    const missing = { paths: { ...config.paths, earningsDir: path.join(tmp, "nope") } } as unknown as ConsoleConfig;
    const s = readEarningsSummary(missing, { mode: "paper", range: "week", day: DAY });
    expect(s.hero.trades).toBe(0);
    expect(s.arms).toEqual([]);
    expect(s.notes.length).toBeGreaterThan(0);
  });
});
