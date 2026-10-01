import { describe, it, expect } from "vitest";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import type { ConsoleConfig } from "../src/config.js";
import { buildSuiteReport } from "../src/services/report.js";

/**
 * The suite report is memoised, and the case that memoisation must not break is the one the review
 * exists to make: a session is PROVISIONAL before it is FINAL, and `review-final` rewrites the same
 * `eod-<session>.json` the next morning rather than adding a file.
 *
 * A cache keyed on the review directory would miss that rewrite entirely — a directory's timestamp
 * moves when an entry is added or removed, not when one is edited — and the page would keep serving
 * provisional numbers as settled ones.
 */

function configFor(tmp: string): ConsoleConfig {
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
      overviewDir: path.join(tmp, "overview"),
      technicalsDir: path.join(tmp, "technicals"),
      advisorDir: path.join(tmp, "advisor"),
      adviceDir: path.join(tmp, "state", "advice"),
      meicRiskConfig: path.join(tmp, "config.risk.json"),
      fliesConfig: path.join(tmp, "config", "flies.json"),
    },
  };
}

function writeFactSet(dir: string, session: string, net: number): void {
  fs.mkdirSync(dir, { recursive: true });
  fs.writeFileSync(
    path.join(dir, `eod-${session}.json`),
    JSON.stringify({
      modules: { meic: { ok: true, results: { net, closed: 1, wins: 1, losses: 0 } } },
    }),
  );
}

describe("the suite report's cache", () => {
  it("picks up a provisional session being restated as final", () => {
    const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "console-report-cache-"));
    const config = configFor(tmp);
    const reviewDir = config.paths.reviewDir;

    writeFactSet(reviewDir, "2026-08-19", 100);
    expect(buildSuiteReport(config).suite.net).toBe(100);

    // Same path, new contents — exactly what review-final does to review-provisional's artifact.
    // The restatement lands the NEXT MORNING, so the timestamp is advanced to model that gap
    // rather than rewriting inside the same millisecond, which no scheduled pair of jobs does.
    writeFactSet(reviewDir, "2026-08-19", 250);
    const restated = path.join(reviewDir, "eod-2026-08-19.json");
    const nextMorning = new Date(Date.now() + 18 * 3600 * 1000);
    fs.utimesSync(restated, nextMorning, nextMorning);

    expect(buildSuiteReport(config).suite.net).toBe(250);
  });

  it("picks up a newly added session", () => {
    const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "console-report-cache-"));
    const config = configFor(tmp);

    writeFactSet(config.paths.reviewDir, "2026-08-19", 100);
    expect(buildSuiteReport(config).suite.net).toBe(100);

    writeFactSet(config.paths.reviewDir, "2026-08-20", 40);

    const after = buildSuiteReport(config);
    expect(after.suite.net).toBe(140);
    expect(after.daily.map((d) => d.session)).toEqual(["2026-08-19", "2026-08-20"]);
  });

  it("serves a repeated call without the fact sets changing", () => {
    const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "console-report-cache-"));
    const config = configFor(tmp);
    writeFactSet(config.paths.reviewDir, "2026-08-19", 100);

    expect(buildSuiteReport(config)).toEqual(buildSuiteReport(config));
  });
});

describe("the suite report's measurement breaks", () => {
  it("carries each drawn module's breaks inside the curve's range, read past the cache", async () => {
    const Database = (await import("better-sqlite3")).default;
    const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "console-report-breaks-"));
    const config = configFor(tmp);
    writeFactSet(config.paths.reviewDir, "2026-08-19", 100);
    writeFactSet(config.paths.reviewDir, "2026-08-20", 40);

    fs.mkdirSync(config.paths.meicDir, { recursive: true });
    const db = new Database(path.join(config.paths.meicDir, "paper_trades.db"));
    db.exec(`CREATE TABLE measurement_breaks (id INTEGER PRIMARY KEY, break_date TEXT, scope TEXT, kind TEXT,
             reason TEXT, detail TEXT, created_at TEXT)`);
    const add = db.prepare("INSERT INTO measurement_breaks (break_date, scope, kind, reason) VALUES (?, ?, ?, ?)");
    add.run("2026-08-01", "*", "entry_rules", "before the curve starts"); // outside the range drawn
    add.run("2026-08-20", "control", "cadence", "tick cadence 30s -> 60s");
    add.run("2999-01-01", "*", "entry_rules", "scheduled, not yet happened"); // after today: not drawn

    const first = buildSuiteReport(config);
    expect(first.breaks["meic"]?.map((b) => [b.date, b.key, b.scope])).toEqual([["2026-08-20", "cadence", "control"]]);

    // A break journaled after the report was cached still shows: the fact sets did not move.
    add.run("2026-08-19", "*", "gate", "a new gate");
    db.close();
    expect(buildSuiteReport(config).breaks["meic"]?.map((b) => b.date)).toEqual(["2026-08-20", "2026-08-19"]);
  });
});

describe("the evidence clock", () => {
  it("knows every module's breaks, restarts only on whole-book ones, and never says 'no break' falsely", async () => {
    const Database = (await import("better-sqlite3")).default;
    const { readDesk } = await import("../src/readers/desk.js");
    const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "console-evidence-"));
    // readDesk reads every module, so it needs every module's home; all but meic's stay empty.
    const base = configFor(tmp);
    const config = {
      ...base,
      paths: {
        ...base.paths,
        calendarsDir: path.join(tmp, "calendars"),
        pmccDir: path.join(tmp, "pmcc"),
        curveDir: path.join(tmp, "curve"),
        bwbDir: path.join(tmp, "bwb"),
        pmccConfigCandidates: [],
        calendarsConfigCandidates: [],
        curveConfigCandidates: [],
      },
    } as ConsoleConfig;
    for (const s of ["2026-08-19", "2026-08-20", "2026-08-21"]) writeFactSet(config.paths.reviewDir, s, 10);

    fs.mkdirSync(config.paths.meicDir, { recursive: true });
    const db = new Database(path.join(config.paths.meicDir, "paper_trades.db"));
    db.exec(`CREATE TABLE measurement_breaks (id INTEGER PRIMARY KEY, break_date TEXT, scope TEXT, kind TEXT,
             reason TEXT, detail TEXT, created_at TEXT)`);
    const add = db.prepare("INSERT INTO measurement_breaks (break_date, scope, kind, reason) VALUES (?, ?, ?, ?)");
    add.run("2026-08-01", "*", "era", "before the curve starts"); // predates every session drawn
    add.run("2026-08-20", "bp-5k", "arm_added", "an arm added"); // arm-scoped: not the module's clock
    db.close();

    const meic = readDesk(config).evidence.find((r) => r.module === "meic");
    // The whole-book break predates the curve, and is still the clock's anchor -- not "no break".
    expect(meic?.lastBreakDate).toBe("2026-08-01");
    expect(meic?.sessionsSince).toBe(3);
    expect(meic?.lastBreakReason).toContain("1 arm-scoped break");
  });
});

describe("the live desk", () => {
  it("names the paper-only modules as having no live path, never as a live zero", async () => {
    const { readDeskLive } = await import("../src/readers/desk.js");
    const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "console-desk-live-"));
    const base = configFor(tmp);
    const config = {
      ...base,
      paths: {
        ...base.paths,
        calendarsDir: path.join(tmp, "calendars"),
        pmccDir: path.join(tmp, "pmcc"),
        curveDir: path.join(tmp, "curve"),
        bwbDir: path.join(tmp, "bwb"),
        pmccConfigCandidates: [],
        calendarsConfigCandidates: [],
        curveConfigCandidates: [],
      },
    } as ConsoleConfig;
    const live = readDeskLive(config);
    expect(live.mode).toBe("live");
    for (const m of ["calendars", "pmcc", "curve"]) {
      const exp = live.exposure.find((r) => r.module === m);
      const ent = live.entries.find((r) => r.module === m);
      expect(exp).toMatchObject({ available: false, open: null, note: "paper-only · no live path" });
      expect(ent).toMatchObject({ available: false, note: "paper-only · no live path" });
    }
    // The four with a live path are rows, in the paper card's order.
    expect(live.exposure.map((r) => r.module)).toEqual(["meic", "flies", "earnings", "calendars", "pmcc", "curve", "bwb"]);
    expect(live.exposure.filter((r) => r.available).map((r) => r.module)).toEqual(["meic", "flies", "earnings", "bwb"]);
  });
});

describe("live flies entries", () => {
  it("come from the decisions journal and the session's positions, not the empty attempts table", async () => {
    const Database = (await import("better-sqlite3")).default;
    const { readFliesLiveEntries } = await import("../src/readers/desk.js");
    const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "console-flies-live-"));
    const config = configFor(tmp);
    fs.mkdirSync(config.paths.fliesDir, { recursive: true });
    const db = new Database(path.join(config.paths.fliesDir, "live_trades.db"));
    db.exec(`CREATE TABLE fly_positions (id INTEGER PRIMARY KEY, trade_date TEXT, status TEXT);
             CREATE TABLE fly_decisions (id INTEGER PRIMARY KEY, trade_date TEXT, reason TEXT, accepted INTEGER, occurrences INTEGER);
             CREATE TABLE fly_entry_attempts (id INTEGER PRIMARY KEY, trade_date TEXT, outcome TEXT);`);
    const pos = db.prepare("INSERT INTO fly_positions (trade_date, status) VALUES (?, ?)");
    for (const st of ["settled", "settled", "open", "cancelled"]) pos.run("2026-09-30", st);
    pos.run("2026-09-29", "settled"); // another session: not counted
    const dec = db.prepare("INSERT INTO fly_decisions (trade_date, reason, accepted, occurrences) VALUES (?, ?, ?, ?)");
    dec.run("2026-09-30", "duplicate_structure", 0, 215);
    dec.run("2026-09-30", "outside_entry_window", 0, 88);
    dec.run("2026-09-30", "entered", 1, 4); // accepted: not a refusal
    db.close();
    expect(readFliesLiveEntries(config, "2026-09-30")).toEqual({
      filled: 3,
      noFill: 1,
      refused: 303,
      topRefusal: "duplicate_structure ×215",
    });
    expect(readFliesLiveEntries(config, null)).toEqual({ filled: 0, refused: 0, noFill: 0, topRefusal: null });
  });
});
