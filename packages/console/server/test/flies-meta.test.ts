import { describe, it, expect, beforeAll } from "vitest";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import Database from "better-sqlite3";
import type { ConsoleConfig } from "../src/config.js";
import { readFliesMeta, eraClause, CURRENT_ERA } from "../src/readers/flies.js";

/**
 * The scope selects' options, and the day every Today card resolves to.
 *
 * `withReadOnlyDb` returns its fallback whenever the callback throws, which is right for an absent
 * store and unhelpful for a malformed query: a broken statement here returns `{arms: [], dates: [],
 * symbols: []}`, indistinguishable from a module that has never run, and every filter on the page
 * quietly empties. That is exactly what a bad column reference in the dates query did, so this
 * asserts the payload is populated rather than merely well-shaped.
 */

let config: ConsoleConfig;
const TODAY = "2026-08-20";
const YESTERDAY = "2026-08-19";

function seed(dir: string): void {
  fs.mkdirSync(dir, { recursive: true });
  const db = new Database(path.join(dir, "paper_trades.db"));
  db.exec(`
    CREATE TABLE fly_positions (
      id INTEGER PRIMARY KEY, trade_date TEXT, symbol TEXT, arm TEXT, pnl REAL
    );
    CREATE TABLE fly_iterations (
      id INTEGER PRIMARY KEY, iteration_ts TEXT, trade_date TEXT, symbol TEXT, arm TEXT
    );
  `);
  const pos = db.prepare("INSERT INTO fly_positions (trade_date, symbol, arm, pnl) VALUES (?,?,?,?)");
  pos.run(YESTERDAY, CURRENT_ERA.symbol, "control", 10);
  pos.run(YESTERDAY, CURRENT_ERA.symbol, "width-5", -5);
  // TODAY has iterated all morning without filling anything — the 2026-08-20 shape.
  const it = db.prepare("INSERT INTO fly_iterations (iteration_ts, trade_date, symbol, arm) VALUES (?,?,?,?)");
  it.run(`${TODAY}T10:00:00-04:00`, TODAY, CURRENT_ERA.symbol, "control");
  db.close();
}

beforeAll(() => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "console-flies-meta-"));
  seed(path.join(tmp, "flies"));
  config = {
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
      scoutDir: path.join(tmp, "scout"),
      reviewDir: path.join(tmp, "review"),
      overviewDir: path.join(tmp, "overview"),
      advisorDir: path.join(tmp, "advisor"),
      adviceDir: path.join(tmp, "state", "advice"),
      meicRiskConfig: path.join(tmp, "config.risk.json"),
      fliesConfig: path.join(tmp, "config", "flies.json"),
    },
  };
});

describe("the flies scope payload", () => {
  it("is populated, not silently empty", () => {
    const meta = readFliesMeta(config, "paper");
    expect(meta.arms).toContain("control");
    expect(meta.symbols).toEqual([CURRENT_ERA.symbol]);
    expect(meta.dates.length).toBeGreaterThan(0);
  });

  it("lists a session the loop ran even before anything filled", () => {
    const meta = readFliesMeta(config, "paper");
    expect(meta.dates).toContain(TODAY);
    expect(meta.dates[0]).toBe(TODAY);
  });

  it("orders newest first, which is what the page resolves as its latest day", () => {
    const meta = readFliesMeta(config, "paper");
    expect(meta.dates).toEqual([...meta.dates].sort().reverse());
    expect(meta.dates).toContain(YESTERDAY);
  });
});

describe("the era scope", () => {
  it("offers every declared era, each readable alone", () => {
    const meta = readFliesMeta(config, "paper");
    expect(meta.eras.map((e) => e.era)).toEqual(["spx", "xsp", "spx-early"]);
    expect(meta.currentEra).toBe("spx");
  });

  it("counts each era against this store rather than dropping the empty ones", () => {
    const meta = readFliesMeta(config, "paper");
    const byKey = Object.fromEntries(meta.eras.map((e) => [e.era, e.trades]));
    expect(byKey["spx"]).toBeGreaterThan(0);
    // The fixture holds no XSP book. Reporting 0 is the point: an era this store never traded is a
    // known quantity, where a missing option is indistinguishable from a broken filter.
    expect(byKey["xsp"]).toBe(0);
  });

  it("bounds a closed era at both ends", () => {
    expect(eraClause("xsp")).toEqual({
      sql: "symbol = ? AND trade_date >= ? AND trade_date <= ?",
      params: ["XSP", "2026-07-29", "2026-07-31"],
    });
    expect(eraClause("spx-early")).toEqual({
      sql: "symbol = ? AND trade_date <= ?",
      params: ["SPX", "2026-07-28"],
    });
  });

  it("pools only on an explicit ALL, and falls back to the default on anything unknown", () => {
    expect(eraClause("ALL").sql).toBeNull();
    expect(eraClause("nonsense")).toEqual(eraClause("spx"));
    expect(eraClause(null)).toEqual(eraClause("spx"));
  });
});
