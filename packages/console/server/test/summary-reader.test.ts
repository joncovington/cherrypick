import { describe, it, expect, afterEach } from "vitest";
import path from "node:path";
import fs from "node:fs";
import os from "node:os";
import Database from "better-sqlite3";
import {
  readTradeSummary,
  parseSummaryFilter,
  resetSummaryReads,
  SUMMARY_SCHEMA,
  ROR_LABEL,
} from "../src/readers/summary.js";
import { setSummaryCaller } from "../src/services/summaryBridge.js";
import type { ConsoleConfig } from "../src/config.js";
import type { SummarySlice } from "../src/services/summaryBridge.js";

/**
 * `readTradeSummary` composes the Reports summary page's one payload over the modules'
 * `core.metrics summary` slices. What's worth pinning here:
 * - the module->schema->dbPath mapping (a wrong entry would silently read the wrong ledger);
 * - that the suite epoch bounds the read and pmcc rides its own stamped era;
 * - that a module with no live ledger refuses a LIVE read visibly, per slice, never silently;
 * - that an absent store degrades ITS slice with the error riding at the top, and the page
 *   still carries the modules that did read ("one absent db degrades its own slice and never
 *   the page");
 * - the money rows' identity THROUGH the composition: by-arm nets add up to the hero's, gross
 *   − fees = net (the suite's one layout, now over every module).
 *
 * The first describe runs the REAL caller — Python and TypeScript in one test — over fixture
 * ledgers, which is the strongest pin a bridge can have (the bridge test's swapped caller only
 * proves the plumbing). It skips, visibly, when `cherrypick.core` is not importable.
 */

function fakeConfig(home: string, modules?: Record<string, { enabled: boolean }>): ConsoleConfig {
  const orchestratorConfig = path.join(home, "config.json");
  fs.writeFileSync(
    orchestratorConfig,
    JSON.stringify({ data_epoch: { date: "2026-08-01", note: "test epoch" }, ...(modules ? { modules } : {}) }),
  );
  const data = path.join(home, "data");
  return {
    port: 0,
    paths: {
      cherrypick: home,
      streamCacheDb: "",
      watchdogLast: "",
      orchestratorConfig,
      consoleData: "",
      meicDir: path.join(data, "meic"),
      fliesDir: path.join(data, "flies"),
      earningsDir: path.join(data, "earnings"),
      calendarsDir: path.join(data, "calendars"),
      pmccDir: path.join(data, "pmcc"),
      curveDir: path.join(data, "curve"),
      bwbDir: path.join(data, "bwb"),
      gexDir: "",
      reviewDir: "",
      overviewDir: "",
      advisorDir: "",
      adviceDir: "",
      meicRiskConfig: "",
      fliesConfig: "",
      pmccConfigCandidates: [],
      calendarsConfigCandidates: [],
      curveConfigCandidates: [],
      contangoDir: path.join(data, "contango"),
      contangoConfigCandidates: [],
    },
  };
}

/** A two-session, two-arm meic ledger. Capital = (wing_width − net_credit) × mult × qty. */
function makeMeicLedger(dir: string): void {
  fs.mkdirSync(dir, { recursive: true });
  const db = new Database(path.join(dir, "paper_trades.db"));
  db.exec(
    "CREATE TABLE ic_trades (symbol TEXT, risk_profile TEXT, pnl REAL, fees REAL, exit_time TEXT, " +
      "slippage_dollars REAL, wing_width REAL, net_credit REAL, quantity INTEGER, dollar_multiplier REAL)",
  );
  db.prepare(
    "INSERT INTO ic_trades (symbol, risk_profile, pnl, fees, exit_time, slippage_dollars, wing_width, net_credit, quantity, dollar_multiplier) VALUES (?,?,?,?,?,?,?,?,?,?)",
  ).run("SPX", "control", 120.0, 5.0, "2026-08-20T15:45:00", 2.0, 10.0, 3.0, 1, 100.0);
  db.prepare(
    "INSERT INTO ic_trades (symbol, risk_profile, pnl, fees, exit_time, slippage_dollars, wing_width, net_credit, quantity, dollar_multiplier) VALUES (?,?,?,?,?,?,?,?,?,?)",
  ).run("SPX", "control", -60.0, 5.0, "2026-08-21T15:45:00", 2.0, 10.0, 3.0, 1, 100.0);
  db.prepare(
    "INSERT INTO ic_trades (symbol, risk_profile, pnl, fees, exit_time, slippage_dollars, wing_width, net_credit, quantity, dollar_multiplier) VALUES (?,?,?,?,?,?,?,?,?,?)",
  ).run("SPX", "aggressive", 300.0, 8.0, "2026-08-20T15:45:00", 3.0, 20.0, 6.0, 1, 100.0);
  db.close();
}

/** A flies ledger: settled rows carry NO capital (a legged book's risk depends on completion). */
function makeFliesLedger(dir: string): void {
  fs.mkdirSync(dir, { recursive: true });
  const db = new Database(path.join(dir, "paper_trades.db"));
  db.exec(
    "CREATE TABLE fly_positions (symbol TEXT, arm TEXT, status TEXT, gross_pnl REAL, fees REAL, entry_mode TEXT, trade_date TEXT)",
  );
  db.prepare("INSERT INTO fly_positions VALUES (?,?,?,?,?,?,?)").run("SPX", "control", "settled", 40.0, 1.5, "outright", "2026-08-20");
  db.prepare("INSERT INTO fly_positions VALUES (?,?,?,?,?,?,?)").run("SPX", "control", "settled", -25.0, 1.5, "outright", "2026-08-21");
  db.close();
}

let pythonOk: boolean | null = null;
function pythonAvailable(): boolean {
  if (pythonOk === null) {
    try {
      const { spawnSync } = require("node:child_process") as typeof import("node:child_process");
      const r = spawnSync(suitePython(), ["-c", "import cherrypick.core.metrics"], { encoding: "utf-8" });
      pythonOk = r.status === 0;
    } catch {
      pythonOk = false;
    }
  }
  return pythonOk;
}

import { suitePython } from "@console/shared";

describe("readTradeSummary over the REAL core caller (skips without cherrypick.core)", () => {
  let config: ConsoleConfig;
  let home: string;

  afterEach(() => {
    resetSummaryReads();
  });

  it("composes one payload whose slices are one population, money rows adding up", (ctx) => {
    if (!pythonAvailable()) return ctx.skip();
    home = fs.mkdtempSync(path.join(os.tmpdir(), "console-summary-test-"));
    config = fakeConfig(home);
    makeMeicLedger(config.paths.meicDir);
    makeFliesLedger(config.paths.fliesDir);
    const out = readTradeSummary(config, parseSummaryFilter({ module: "meic", range: "all", mode: "paper" }));
    expect(out.modulesRead).toContain("meic");
    const s = out.slices.find((x) => x.module === "meic");
    expect(s).toBeDefined();
    expect(s!.hero.trades).toBe(3);
    expect(s!.hero.gross).toBe(360.0); // 120 − 60 + 300
    expect(s!.hero.fees).toBe(18.0); // 5 + 5 + 8
    expect(s!.hero.net).toBe(342.0); // 115 − 65 + 292
    // by-arm nets add up to the hero's, gross − fees = net, per the suite's one layout.
    expect(s!.byArm.reduce((t, a) => t + a.net, 0)).toBeCloseTo(s!.hero.net, 2);
    expect(s!.hero.gross - s!.hero.fees).toBeCloseTo(s!.hero.net, 2);
    // 342 / 2800 = 12.2143% — the unrounded numerator over the capital sum, full coverage.
    expect(s!.return.capitalAtRisk).toBe(2800.0);
    expect(s!.return.returnOnRiskRawPct).toBeCloseTo(12.2143, 4);
    expect(s!.return.coverage).toBe("3/3");
    // facets are WHOLE-LEDGER under no filter, arms sorted.
    expect(s!.facets.arms).toEqual(["aggressive", "control"]);
    expect(s!.facets.symbols).toEqual(["SPX"]);
  });

  it("day scopes to one session; symbol/arm narrow slices and never facets", (ctx) => {
    if (!pythonAvailable()) return ctx.skip();
    home = fs.mkdtempSync(path.join(os.tmpdir(), "console-summary-test-"));
    config = fakeConfig(home);
    makeMeicLedger(config.paths.meicDir);
    const out = readTradeSummary(
      config,
      parseSummaryFilter({ module: "meic", range: "day", mode: "paper", session: "2026-08-20", arm: "control" }),
    );
    const s = out.slices[0];
    expect(s.hero.trades).toBe(1);
    expect(s.hero.net).toBe(115.0);
    expect(s.byArm).toHaveLength(1);
    expect(s.facets.arms).toEqual(["aggressive", "control"]); // the select keeps every option
    expect(s.window).toEqual({ from: "2026-08-20", to: "2026-08-20" });
  });

  it("a schema whose rows carry no capital refuses the ratio — flies is n/r, never zero", (ctx) => {
    if (!pythonAvailable()) return ctx.skip();
    home = fs.mkdtempSync(path.join(os.tmpdir(), "console-summary-test-"));
    config = fakeConfig(home);
    makeFliesLedger(config.paths.fliesDir);
    const out = readTradeSummary(config, parseSummaryFilter({ module: "flies", range: "all", mode: "paper" }));
    const s = out.slices[0];
    // 38.5 + (−26.5) = 12.0 — net is gross − fees; flies' fills already price the haircut in.
    expect(s.hero.net).toBe(12.0);
    expect(s.return.returnOnRiskRawPct).toBeNull();
    expect(s.return.coverage).toBe("0/2");
    expect(s.return.capitalAtRisk).toBeNull();
    // flies has no max_profit either — the capture card shows value null (n 0), not a zero ratio.
    expect(s.capture).toEqual({ value: null, n: 0 });
    // The label rule follows the MODULE, riding the slice (the page keeps no table of its own).
    expect(s.returnLabel.capital).toBe("capital at risk");
    expect(ROR_LABEL["contango"].capital).toBe("cash deployed");
  });

  it("an absent store degrades ITS slice with the error at the top; the page still reads", (ctx) => {
    if (!pythonAvailable()) return ctx.skip();
    home = fs.mkdtempSync(path.join(os.tmpdir(), "console-summary-test-"));
    config = fakeConfig(home);
    makeMeicLedger(config.paths.meicDir);
    const out = readTradeSummary(config, parseSummaryFilter({ module: "calendars", range: "all", mode: "paper" }));
    expect(out.modulesRead).not.toContain("calendars");
    expect(out.moduleErrors["calendars"]).toMatch(/no store/);
    const dead = out.slices[0];
    expect(dead.error).toMatch(/no store/);
    expect(dead.hero.trades).toBe(0);
    expect(dead.byArm).toEqual([]);
    expect(dead.return.returnOnRiskRawPct).toBeNull();
  });

  it("a module with no live ledger refuses a LIVE read visibly, per slice", (ctx) => {
    if (!pythonAvailable()) return ctx.skip();
    home = fs.mkdtempSync(path.join(os.tmpdir(), "console-summary-test-"));
    config = fakeConfig(home);
    makeMeicLedger(config.paths.meicDir);
    const out = readTradeSummary(config, parseSummaryFilter({ module: "calendars", range: "all", mode: "live" }));
    expect(out.moduleErrors["calendars"]).toMatch(/no live ledger/);
    expect(out.slices[0].error).toMatch(/no live ledger/);
    expect(out.modulesRead).not.toContain("calendars");
  });

  it("the epoch bounds every schema; pmcc's unstamped rows never match its own era", (ctx) => {
    if (!pythonAvailable()) return ctx.skip();
    home = fs.mkdtempSync(path.join(os.tmpdir(), "console-summary-test-"));
    config = fakeConfig(home);
    // A pre-epoch row (2026-07-01, before the fixture's 2026-08-01 epoch), era NULL: the
    // --start bound removes it, and pmcc's own era filter matches nothing on an unstamped row
    // — "an unstamped row never matches a stamped era" is the module's own rule, honoured by
    // the summary read rather than pooled away.
    fs.mkdirSync(config.paths.pmccDir, { recursive: true });
    const db = new Database(path.join(config.paths.pmccDir, "paper_trades.db"));
    db.exec(
      "CREATE TABLE pmcc_positions (symbol TEXT, arm TEXT, status TEXT, gross_pnl REAL, fees REAL, " +
        "entry_slippage REAL, exit_slippage REAL, net_debit REAL, quantity INTEGER, closed_session TEXT, era TEXT)",
    );
    db.prepare("INSERT INTO pmcc_positions VALUES (?,?,?,?,?,?,?,?,?,?,?)").run(
      "XSP", "control", "closed", 30.0, 1.0, 0.0, 0.0, 50.0, 1, "2026-07-01", null,
    );
    db.close();
    const out = readTradeSummary(config, parseSummaryFilter({ module: "pmcc", range: "all", mode: "paper" }));
    const s = out.slices[0];
    expect(out.moduleErrors["pmcc"]).toBeUndefined(); // the read succeeds — with zero rows
    expect(s.hero.trades).toBe(0);
    expect(s.return.returnOnRiskRawPct).toBeNull();
  });

  it("the switchboard decides the comparison: off modules hold no card, a direct ask states the refusal", (ctx) => {
    if (!pythonAvailable()) return ctx.skip();
    home = fs.mkdtempSync(path.join(os.tmpdir(), "console-summary-test-"));
    config = fakeConfig(home, {
      meic: { enabled: true },
      flies: { enabled: true },
      calendars: { enabled: false },
      pmcc: {}, // declared, no enabled key: the orchestrator's opt-in convention says OFF
    });
    makeMeicLedger(config.paths.meicDir);
    // The comparison: calendars and pmcc hold no card at all (pmcc was never enabled; contango
    // and curve are not even declared, so off with them), and the payload says it hid them.
    const cmp = readTradeSummary(config, parseSummaryFilter({ range: "all", mode: "paper" }));
    expect(cmp.slices.find((s) => s.module === "calendars")).toBeUndefined();
    expect(cmp.slices.find((s) => s.module === "pmcc")).toBeUndefined();
    // Only meic's ledger exists in this fixture; the rest of the comparison read their
    // "no store" refusals and so do not count as read.
    expect(cmp.modulesRead).toEqual(["meic"]);
    // The hidden list rides the module order (meic flies earnings calendars pmcc curve contango bwb).
    expect(cmp.modulesHidden).toEqual(["earnings", "calendars", "pmcc", "curve", "contango", "bwb"]);
    // A DIRECT ask is honoured — the reader wants that module — and the slice states the
    // refusal naming the switch, never numbers that look like a silent empty book.
    const direct = readTradeSummary(config, parseSummaryFilter({ module: "calendars", range: "all", mode: "paper" }));
    expect(direct.modulesHidden).toEqual(["earnings", "calendars", "pmcc", "curve", "contango", "bwb"]);
    expect(direct.moduleErrors["calendars"]).toMatch(/switched off in the orchestrator config/);
    expect(direct.slices[0].error).toMatch(/switched off/);
    expect(direct.slices[0].hero.trades).toBe(0);
    // An enabled module with a real ledger still reads as before.
    const meic = readTradeSummary(config, parseSummaryFilter({ module: "meic", range: "all", mode: "paper" }));
    expect(meic.modulesHidden).toEqual(["earnings", "calendars", "pmcc", "curve", "contango", "bwb"]);
    expect(meic.slices[0].hero.trades).toBe(3);
  });

  it("a config file with no modules block hides nothing; a broken one cannot hide data either", (ctx) => {
    if (!pythonAvailable()) return ctx.skip();
    home = fs.mkdtempSync(path.join(os.tmpdir(), "console-summary-test-"));
    config = fakeConfig(home); // no modules block at all
    makeMeicLedger(config.paths.meicDir);
    const out = readTradeSummary(config, parseSummaryFilter({ range: "all", mode: "paper" }));
    expect(out.modulesHidden).toEqual([]);
    expect(out.slices.find((s) => s.module === "calendars")).toBeDefined();
  });
});

describe("the summary reader's TypeScript-side parts", () => {
  afterEach(() => {
    setSummaryCaller();
    resetSummaryReads();
  });

  it("parseSummaryFilter validates every field", () => {
    const f = parseSummaryFilter({ module: "meic", range: "week", mode: "live", session: "2026-08-20", symbol: "SPX", arm: "control" });
    expect(f).toEqual({ module: "meic", session: "2026-08-20", symbol: "SPX", arm: "control", range: "week", mode: "live" });
    const bad = parseSummaryFilter({ module: "gex", range: "hour", mode: "side", session: "not-a-date" });
    expect(bad.module).toBeNull(); // not a summary module
    expect(bad.range).toBe("day"); // an unknown range falls back, never widens
    expect(bad.mode).toBe("paper"); // anything but live is paper
    expect(bad.session).toBeNull(); // an unparseable date is not a date
  });

  it("maps each module to its ledger schema (a wrong entry would read the wrong ledger)", () => {
    expect(SUMMARY_SCHEMA["meic"]).toBe("meic_ic");
    expect(SUMMARY_SCHEMA["flies"]).toBe("fly_book");
    expect(SUMMARY_SCHEMA["earnings"]).toBe("earnings");
    expect(SUMMARY_SCHEMA["calendars"]).toBe("dc_week");
    expect(SUMMARY_SCHEMA["pmcc"]).toBe("pmcc");
    expect(SUMMARY_SCHEMA["curve"]).toBe("curve_vx");
    expect(SUMMARY_SCHEMA["contango"]).toBe("contango_etf");
    expect(SUMMARY_SCHEMA["bwb"]).toBe("bwb_132");
  });

  it("passes the epoch bound and the argv through to the bridge", () => {
    const seen: unknown[] = [];
    setSummaryCaller((a) => {
      seen.push(a);
      return {
        ok: true,
        schema: a.schema,
        window: { from: null, to: null },
        hero: { trades: 0, sessions: 0, net: 0, gross: 0, fees: 0, wins: 0, win_pct: null, capital: null, capital_coverage: "0/0" },
        return: { value: null, coverage: "0/0" },
        by_arm: {},
        daily: [],
        facets: { sessions: [], symbols: [], arms: [] },
        capture: { value: null, n: 0 },
      };
    });
    const home = fs.mkdtempSync(path.join(os.tmpdir(), "console-summary-test-"));
    const config = fakeConfig(home);
    readTradeSummary(config, parseSummaryFilter({ module: "meic", range: "week", session: "2026-08-20" }));
    const a = seen[0] as { dbPath: string; schema: string; session: string | null; arm: string | null; width: string; start: string | null; era: string | null };
    expect(a.dbPath).toBe(path.join(config.paths.meicDir, "paper_trades.db"));
    expect(a.schema).toBe("meic_ic");
    expect(a.session).toBe("2026-08-20");
    expect(a.width).toBe("week");
    expect(a.start).toBe("2026-08-01"); // the fixture's epoch bound rides through
    expect(a.era).toBeNull(); // only pmcc carries an era
  });

  it("memoises per filter key — the page polls, the subprocess must not", () => {
    let calls = 0;
    setSummaryCaller((a) => {
      calls += 1;
      return {
        ok: true,
        schema: a.schema,
        window: { from: null, to: null },
        hero: { trades: 0, sessions: 0, net: 0, gross: 0, fees: 0, wins: 0, win_pct: null, capital: null, capital_coverage: "0/0" },
        return: { value: null, coverage: "0/0" },
        by_arm: {},
        daily: [],
        facets: { sessions: [], symbols: [], arms: [] },
        capture: { value: null, n: 0 },
      };
    });
    const home = fs.mkdtempSync(path.join(os.tmpdir(), "console-summary-test-"));
    const config = fakeConfig(home);
    const f = parseSummaryFilter({ module: "meic", range: "day" });
    readTradeSummary(config, f);
    readTradeSummary(config, f);
    expect(calls).toBe(1);
    readTradeSummary(config, parseSummaryFilter({ module: "meic", range: "week" })); // a new key
    expect(calls).toBe(2);
  });
});
