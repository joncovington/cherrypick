import { describe, it, expect } from "vitest";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import Database from "better-sqlite3";
import type { ConsoleConfig } from "../src/config.js";
import { readAdvisedPairs } from "../src/readers/pairs.js";
import { closePooledDbs } from "../src/readers/db.js";
import type { ModulePerformanceGroup } from "../src/readers/performance.js";

/**
 * `readAdvisedPairs` pairs an `advised:<base>` performance group to its control by stripping the
 * prefix, counts the sessions they both actually recorded a net for, and looks up the experiment
 * that produced the twin in advisor.db -- never recomputing the verdict itself (packages/advisor's
 * own rule: verdicts are computed there and stored, a second computation would be a second
 * opinion free to drift).
 */

function tmpConfig(): { config: ConsoleConfig; tmp: string } {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "console-pairs-"));
  const config = {
    port: 0,
    paths: {
      cherrypick: tmp,
      streamCacheDb: "",
      watchdogLast: "",
      orchestratorConfig: "",
      consoleData: "",
      meicDir: "",
      fliesDir: "",
      earningsDir: "",
      calendarsDir: "",
      pmccDir: "",
      curveDir: "",
      bwbDir: "",
      gexDir: "",
      reviewDir: "",
      overviewDir: "",
      advisorDir: path.join(tmp, "advisor"),
      adviceDir: "",
      meicRiskConfig: "",
      fliesConfig: "",
      pmccConfigCandidates: [],
      calendarsConfigCandidates: [],
      curveConfigCandidates: [],
    },
  } as unknown as ConsoleConfig;
  return { config, tmp };
}

function group(tag: string, sessions: string[]): ModulePerformanceGroup {
  return {
    tag,
    reading: {},
    sessionNets: sessions.map((s) => [s, 1.0] as [string, number]),
    tradeNets: [],
  };
}

describe("readAdvisedPairs", () => {
  it("pairs an advised group to its control by stripping the prefix", () => {
    const { config } = tmpConfig();
    const groups = [
      group("control", ["2026-08-20", "2026-08-21"]),
      group("advised:control", ["2026-08-21"]),
    ];
    const out = readAdvisedPairs(config, "curve", groups);
    expect(out).toHaveLength(1);
    expect(out[0]).toMatchObject({ advised: "advised:control", base: "control", sessionsPaired: 1 });
  });

  it("counts only sessions BOTH books actually recorded, not the advised book's own count", () => {
    const { config } = tmpConfig();
    const groups = [
      group("control", ["2026-08-20", "2026-08-21", "2026-08-24"]),
      group("advised:control", ["2026-08-20", "2026-08-21", "2026-08-22", "2026-08-25"]),
    ];
    const out = readAdvisedPairs(config, "curve", groups);
    // advised has 4 sessions, control has 3; only 08-20 and 08-21 appear in both.
    expect(out[0].sessionsPaired).toBe(2);
  });

  it("reports sessionsPaired=0 rather than omitting the pair when the base has no data in this window", () => {
    const { config } = tmpConfig();
    const groups = [group("advised:control", ["2026-08-21"])];
    const out = readAdvisedPairs(config, "curve", groups);
    expect(out).toHaveLength(1);
    expect(out[0].sessionsPaired).toBe(0);
  });

  it("returns nothing for a module with no advised group at all", () => {
    const { config } = tmpConfig();
    const groups = [group("control", ["2026-08-20"]), group("noflip", ["2026-08-20"])];
    expect(readAdvisedPairs(config, "curve", groups)).toEqual([]);
  });

  it("looks up the experiment id and stored underpowered verdict from advisor.db", () => {
    const { config, tmp } = tmpConfig();
    fs.mkdirSync(path.join(tmp, "advisor"), { recursive: true });
    const db = new Database(path.join(tmp, "advisor", "advisor.db"));
    db.exec(
      "CREATE TABLE experiments (id TEXT, module TEXT, base_profile TEXT, verdict_json TEXT, created_at TEXT)",
    );
    db.prepare("INSERT INTO experiments VALUES (?,?,?,?,?)").run(
      "exp-1",
      "curve",
      "control",
      JSON.stringify({ underpowered: true, pairs: [] }),
      "2026-08-20T00:00:00",
    );
    db.close();

    const groups = [group("control", []), group("advised:control", [])];
    const out = readAdvisedPairs(config, "curve", groups);
    closePooledDbs();
    expect(out[0]).toMatchObject({ experimentId: "exp-1", underpowered: true });
  });

  it("picks the MOST RECENT experiment when more than one exists for the same base", () => {
    const { config, tmp } = tmpConfig();
    fs.mkdirSync(path.join(tmp, "advisor"), { recursive: true });
    const db = new Database(path.join(tmp, "advisor", "advisor.db"));
    db.exec(
      "CREATE TABLE experiments (id TEXT, module TEXT, base_profile TEXT, verdict_json TEXT, created_at TEXT)",
    );
    db.prepare("INSERT INTO experiments VALUES (?,?,?,?,?)").run(
      "exp-old",
      "curve",
      "control",
      JSON.stringify({ underpowered: false }),
      "2026-08-01T00:00:00",
    );
    db.prepare("INSERT INTO experiments VALUES (?,?,?,?,?)").run(
      "exp-new",
      "curve",
      "control",
      JSON.stringify({ underpowered: true }),
      "2026-08-20T00:00:00",
    );
    db.close();

    const groups = [group("control", []), group("advised:control", [])];
    const out = readAdvisedPairs(config, "curve", groups);
    closePooledDbs();
    expect(out[0]).toMatchObject({ experimentId: "exp-new", underpowered: true });
  });

  it("joins on base_profile before any :strategy suffix (earnings' advised:<base>:<strategy> shape)", () => {
    const { config, tmp } = tmpConfig();
    fs.mkdirSync(path.join(tmp, "advisor"), { recursive: true });
    const db = new Database(path.join(tmp, "advisor", "advisor.db"));
    db.exec(
      "CREATE TABLE experiments (id TEXT, module TEXT, base_profile TEXT, verdict_json TEXT, created_at TEXT)",
    );
    db.prepare("INSERT INTO experiments VALUES (?,?,?,?,?)").run(
      "exp-1",
      "earnings",
      "balanced",
      JSON.stringify({ underpowered: false }),
      "2026-08-20T00:00:00",
    );
    db.close();

    const groups = [group("balanced:iron_fly", []), group("advised:balanced:iron_fly", [])];
    const out = readAdvisedPairs(config, "earnings", groups);
    closePooledDbs();
    expect(out[0]).toMatchObject({
      advised: "advised:balanced:iron_fly",
      base: "balanced:iron_fly",
      experimentId: "exp-1",
      underpowered: false,
    });
  });

  it("underpowered stays null when no experiment row exists to ask", () => {
    const { config } = tmpConfig();
    const groups = [group("control", []), group("advised:control", [])];
    const out = readAdvisedPairs(config, "curve", groups);
    expect(out[0]).toMatchObject({ experimentId: null, underpowered: null });
  });
});
