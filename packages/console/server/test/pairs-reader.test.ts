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

  it("looks up the stored underpowered verdict for the experiment the rows are stamped with", () => {
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

    const groups = [group("control", []), group("advised:control@exp-1", [])];
    const out = readAdvisedPairs(config, "curve", groups);
    closePooledDbs();
    expect(out[0]).toMatchObject({ base: "control", experimentId: "exp-1", underpowered: true, unstamped: false });
  });

  it("pairs each stamped experiment separately and leaves the pre-stamp rows as one unstamped pair", () => {
    // Three experiments ran on advised:control in turn; before 2026-09-16 the pair carried the
    // most recent experiment's id over all of their rows.
    const { config } = tmpConfig();
    const groups = [
      group("control", ["2026-08-26", "2026-09-10", "2026-09-15"]),
      group("advised:control", ["2026-08-26"]),
      group("advised:control@exp-2026-09-09-meic-1", ["2026-09-10"]),
      group("advised:control@exp-2026-09-14-meic-1", ["2026-09-15"]),
    ];
    const out = readAdvisedPairs(config, "meic", groups);
    expect(out.map((p) => [p.advised, p.base, p.experimentId, p.unstamped, p.sessionsPaired])).toEqual([
      ["advised:control", "control", null, true, 1],
      ["advised:control@exp-2026-09-09-meic-1", "control", "exp-2026-09-09-meic-1", false, 1],
      ["advised:control@exp-2026-09-14-meic-1", "control", "exp-2026-09-14-meic-1", false, 1],
    ]);
  });

  it("keeps the stamp as the attribution when advisor.db has no row for it", () => {
    const { config } = tmpConfig();
    const out = readAdvisedPairs(config, "curve", [group("advised:control@exp-gone", [])]);
    expect(out[0]).toMatchObject({ experimentId: "exp-gone", underpowered: null, unstamped: false });
  });

  it("splits earnings' strategy-suffixed tags without losing the strategy", () => {
    const { config } = tmpConfig();
    const groups = [
      group("strat_test:iron_fly", ["2026-09-15"]),
      group("advised:strat_test:iron_fly@exp-2026-08-31-earnings-1", ["2026-09-15"]),
    ];
    const out = readAdvisedPairs(config, "earnings", groups);
    expect(out[0]).toMatchObject({
      base: "strat_test:iron_fly",
      experimentId: "exp-2026-08-31-earnings-1",
      sessionsPaired: 1,
    });
  });

  it("an unstamped earnings twin keeps its :strategy base and attributes no experiment", () => {
    // Before 2026-09-16 this joined advisor.db on base_profile and handed the pair whichever
    // experiment was most recent; the stamp on the rows is the attribution now, and rows without
    // one are history the pair cannot assign.
    const { config } = tmpConfig();
    const groups = [group("balanced:iron_fly", []), group("advised:balanced:iron_fly", [])];
    const out = readAdvisedPairs(config, "earnings", groups);
    expect(out[0]).toMatchObject({
      advised: "advised:balanced:iron_fly",
      base: "balanced:iron_fly",
      experimentId: null,
      unstamped: true,
      underpowered: null,
    });
  });

  it("underpowered stays null when no experiment row exists to ask", () => {
    const { config } = tmpConfig();
    const groups = [group("control", []), group("advised:control", [])];
    const out = readAdvisedPairs(config, "curve", groups);
    expect(out[0]).toMatchObject({ experimentId: null, underpowered: null });
  });
});
