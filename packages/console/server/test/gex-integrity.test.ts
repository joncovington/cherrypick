import { describe, it, expect, beforeAll } from "vitest";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import Database from "better-sqlite3";
import type { ConsoleConfig } from "../src/config.js";
import { readGex } from "../src/readers/gex.js";

/**
 * GEX journals no measurement breaks; what makes its numbers untrustworthy is staleness and
 * truncation. Both have bitten: a gamma-flip read failed silently for a month and left bwb's flip
 * book unable to fire, and `daily_closes` -- the suite's only multi-year series -- froze for SPX for
 * 22 sessions while every other symbol stayed current, with nothing on any page to show it.
 */

let config: ConsoleConfig;
const NOW = Math.floor(Date.now() / 1000);
const iso = (offsetSeconds: number) => new Date((NOW - offsetSeconds) * 1000).toISOString();

beforeAll(() => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "console-gex-"));
  fs.mkdirSync(path.join(tmp, "gex"), { recursive: true });
  const db = new Database(path.join(tmp, "gex", "gex_history.db"));
  db.exec(`
    CREATE TABLE gex_regime_history (symbol TEXT, trade_date TEXT, ts TEXT, spot REAL,
      net_gex REAL, net_gex_vol REAL, zero_gamma REAL, call_wall REAL, put_wall REAL, expiration TEXT);
    CREATE TABLE daily_closes (symbol TEXT, trade_date TEXT, close REAL, recorded_at REAL, source TEXT);
  `);
  const r = db.prepare(
    "INSERT INTO gex_regime_history (symbol, trade_date, ts, spot) VALUES (?, ?, ?, ?)",
  );
  r.run("SPX", "2026-08-27", iso(30), 7700);       // fresh
  r.run("SPX", "2026-08-27", iso(600), 7690);
  r.run("XSP", "2026-08-27", iso(4 * 3600), 770);  // hours stale, but STILL on the latest session
  // A retired symbol: last written weeks ago, on an older session. Its rows are history, not
  // staleness, and ageing it would put a permanent warning on the page.
  r.run("QQQ", "2026-07-29", "2026-07-29T16:00:00.000Z", 500);
  const c = db.prepare("INSERT INTO daily_closes (symbol, trade_date, close) VALUES (?, ?, ?)");
  c.run("SPX", "2026-08-27", 7700);
  c.run("SPY", "2026-08-26", 766);                 // one day behind — the ordinary case
  c.run("SKEW", "2025-09-10", 151);                // the real one: a year frozen
  db.close();
  config = {
    port: 0,
    paths: {
      cherrypick: tmp, streamCacheDb: path.join(tmp, "s.db"), watchdogLast: path.join(tmp, "w.json"),
      orchestratorConfig: path.join(tmp, "c.json"), consoleData: path.join(tmp, "console"),
      meicDir: path.join(tmp, "meic"), fliesDir: path.join(tmp, "flies"),
      earningsDir: path.join(tmp, "earnings"), gexDir: path.join(tmp, "gex"),
      scoutDir: path.join(tmp, "scout"), reviewDir: path.join(tmp, "review"),
      overviewDir: path.join(tmp, "overview"), advisorDir: path.join(tmp, "advisor"),
      adviceDir: path.join(tmp, "advice"), meicRiskConfig: path.join(tmp, "r.json"),
      fliesConfig: path.join(tmp, "f.json"),
    },
  };
});

describe("gex reading freshness", () => {
  it("ages the newest row per symbol, because a flip is a claim about now", () => {
    const i = readGex(config).integrity;
    const spx = i.latest.find((r) => r.symbol === "SPX");
    const xsp = i.latest.find((r) => r.symbol === "XSP");
    expect(spx?.ageSeconds).toBeLessThan(120);
    expect(xsp?.ageSeconds).toBeGreaterThan(3600);
  });

  it("does not age a symbol the recorder has retired", () => {
    // gex records SPX alone today; IWM, QQQ and XSP are retired and their last rows are history.
    // Ageing those would put a permanent warning on the page — the failure mode that makes a check
    // worthless. The roster is derived from the latest session, not from a config this package
    // does not own.
    const i = readGex(config).integrity;
    expect(i.latest.map((r) => r.symbol).sort()).toEqual(["SPX", "XSP"]);
    expect(i.latest.find((r) => r.symbol === "QQQ")).toBeUndefined();
  });

  it("reports the session's row count without inventing a threshold", () => {
    // A short session and a stalled recorder both produce a small number, and only the timestamps
    // separate them — so the count is stated and not judged.
    const i = readGex(config).integrity;
    expect(i.sessionDate).toBe("2026-08-27");
    expect(i.sessionRows).toBe(3);
  });
});

describe("gex close-series continuity", () => {
  it("measures staleness against the freshest series, not a calendar", () => {
    // No holiday table needed: if every other close reached today and one sits a year back, that is
    // unambiguous however the trading days fall.
    const i = readGex(config).integrity;
    const by = new Map(i.closeSeries.map((r) => [r.symbol, r]));
    expect(by.get("SPX")?.daysBehind).toBe(0);
    expect(by.get("SPY")?.daysBehind).toBe(1);
    expect(by.get("SKEW")?.daysBehind).toBeGreaterThan(300);
  });

  it("orders the most stale first, since that is the actionable end", () => {
    const i = readGex(config).integrity;
    expect(i.closeSeries[0]?.symbol).toBe("SKEW");
  });

  it("does not flag a series that is merely waiting for tonight's close", () => {
    // SPY one day back is the ordinary mechanism -- a session's close arrives as the NEXT row's
    // prev_day_close -- and flagging it would bury the year-old one in noise.
    const i = readGex(config).integrity;
    const spy = i.closeSeries.find((r) => r.symbol === "SPY");
    expect(spy?.daysBehind).toBeLessThanOrEqual(5);
  });
});
