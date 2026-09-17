import { describe, it, expect, afterEach } from "vitest";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import Database from "better-sqlite3";
import type { ConsoleConfig } from "../src/config.js";
import { readAdvisedPairs } from "../src/readers/pairs.js";
import { closePooledDbs } from "../src/readers/db.js";
import type { ModulePerformanceGroup } from "../src/readers/performance.js";

/**
 * `readAdvisedPairs` pairs each advised performance group to the base it shadows, counts the
 * sessions they both actually recorded a net for, and looks up the experiment that produced the
 * book in advisor.db -- never recomputing the verdict itself (packages/advisor's own rule:
 * verdicts are computed there and stored, a second computation would be a second opinion free to
 * drift).
 *
 * Since 2026-09-17 each experiment writes its own book (`advised:<experiment name>`) and the base
 * is on the experiment's row, not in the tag. These pin the resolution order in
 * `experimentIndex.ts`: the stamp on the rows, then the tag against the experiment's own, then
 * the legacy `advised:<base>` reading for rows no experiment claims.
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

interface SeedExperiment {
  id: string;
  module: string;
  base: string;
  name?: string | null;
  status?: string;
  tag?: string | null;
  underpowered?: boolean | null;
}

/** An advisor store with the columns the reader keys on; `withTagColumn` adds the 2026-09-17
 *  `tag` column so both shapes of store are exercised. */
function seedStore(tmp: string, rows: SeedExperiment[], withTagColumn = false): void {
  fs.mkdirSync(path.join(tmp, "advisor"), { recursive: true });
  const db = new Database(path.join(tmp, "advisor", "advisor.db"));
  db.exec(
    "CREATE TABLE experiments (id TEXT, module TEXT, base_profile TEXT, name TEXT, status TEXT," +
      ` verdict_json TEXT, created_at TEXT${withTagColumn ? ", tag TEXT" : ""})`,
  );
  const ins = db.prepare(
    `INSERT INTO experiments (id, module, base_profile, name, status, verdict_json, created_at${withTagColumn ? ", tag" : ""})` +
      ` VALUES (?,?,?,?,?,?,?${withTagColumn ? ",?" : ""})`,
  );
  rows.forEach((r, i) => {
    const verdict = r.underpowered === undefined || r.underpowered === null ? null : JSON.stringify({ underpowered: r.underpowered, pairs: [] });
    const args: unknown[] = [r.id, r.module, r.base, r.name ?? null, r.status ?? "active", verdict, `2026-09-0${(i % 9) + 1}T00:00:00`];
    if (withTagColumn) args.push(r.tag ?? null);
    ins.run(...args);
  });
  db.close();
}

afterEach(() => closePooledDbs());

describe("readAdvisedPairs", () => {
  it("pairs a legacy advised group to the base its tag names when there is no store to ask", () => {
    const { config } = tmpConfig();
    const groups = [
      group("control", ["2026-08-20", "2026-08-21"]),
      group("advised:control", ["2026-08-21"]),
    ];
    const out = readAdvisedPairs(config, "curve", groups);
    expect(out).toHaveLength(1);
    expect(out[0]).toMatchObject({ advised: "advised:control", base: "control", sessionsPaired: 1, attribution: "none" });
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

  it("two experiments on one base are two pairs against that base, each its own book", () => {
    // The change itself (2026-09-17): the tag names the experiment, the row names the base.
    const { config, tmp } = tmpConfig();
    seedStore(tmp, [
      { id: "exp-2026-09-09-meic-1", module: "meic", base: "control", name: "entry-window-truncate-vs-control", underpowered: true },
      { id: "exp-2026-09-15-meic-1", module: "meic", base: "control", name: "Stop Later", underpowered: false },
    ]);
    const groups = [
      group("control", ["2026-09-10", "2026-09-16"]),
      group("advised:entry-window-truncate-vs-control", ["2026-09-10", "2026-09-16"]),
      group("advised:stop-later", ["2026-09-16"]),
    ];
    const out = readAdvisedPairs(config, "meic", groups);
    expect(out.map((p) => [p.advised, p.base, p.experimentId, p.experimentName, p.attribution, p.unstamped, p.underpowered, p.sessionsPaired])).toEqual([
      ["advised:entry-window-truncate-vs-control", "control", "exp-2026-09-09-meic-1", "entry-window-truncate-vs-control", "tag", false, true, 2],
      // The slug of "Stop Later" is the book name -- derived on a store without the tag column.
      ["advised:stop-later", "control", "exp-2026-09-15-meic-1", "Stop Later", "tag", false, false, 1],
    ]);
  });

  it("prefers the experiment row's own tag column over the slug of its name", () => {
    const { config, tmp } = tmpConfig();
    seedStore(tmp, [{ id: "exp-1", module: "flies", base: "control", name: "renamed since", tag: "advised:original-name" }], true);
    const groups = [group("control", []), group("advised:original-name", []), group("advised:renamed-since", [])];
    const out = readAdvisedPairs(config, "flies", groups);
    expect(out.find((p) => p.advised === "advised:original-name")).toMatchObject({ experimentId: "exp-1", base: "control", attribution: "tag" });
    // The slug of the current name matches nothing once the column says otherwise: legacy reading.
    expect(out.find((p) => p.advised === "advised:renamed-since")).toMatchObject({ experimentId: null, unstamped: true });
  });

  it("a legacy advised:<base> group stamped with an experiment id pairs to THAT experiment's base", () => {
    // Rows retagged later, or written under the old naming with the stamp: the stamp wins and
    // the base is the row's, not the tag's -- here an experiment on width-5 whose rows still sit
    // under advised:control.
    const { config, tmp } = tmpConfig();
    seedStore(tmp, [{ id: "exp-w", module: "meic", base: "width-5", name: "stop-later", underpowered: true }]);
    const groups = [group("control", ["2026-09-10"]), group("width-5", ["2026-09-10"]), group("advised:control@exp-w", ["2026-09-10"])];
    const out = readAdvisedPairs(config, "meic", groups);
    expect(out[0]).toMatchObject({
      advised: "advised:control@exp-w",
      base: "width-5",
      experimentId: "exp-w",
      experimentName: "stop-later",
      attribution: "stamp",
      unstamped: false,
      underpowered: true,
      sessionsPaired: 1,
    });
  });

  it("an unmatched legacy tag pairs to the module's declared base as unstamped history", () => {
    const { config, tmp } = tmpConfig();
    seedStore(tmp, [{ id: "exp-live", module: "curve", base: "control", name: "profit-take-must-clear-fees" }]);
    fs.mkdirSync(path.join(tmp, "config"), { recursive: true });
    fs.writeFileSync(path.join(tmp, "config", "curve.json"), JSON.stringify({ advice: { enabled: true, base_book: "control" } }));
    const groups = [
      group("control", ["2026-09-01"]),
      group("noflip", ["2026-09-01"]),
      // Its own named base has rows: pairs there, not to the declared base.
      group("advised:noflip", ["2026-09-01"]),
      // No such book in this window and no experiment row: falls back to the declared base.
      group("advised:something-old", ["2026-09-01"]),
    ];
    const out = readAdvisedPairs(config, "curve", groups);
    expect(out.find((p) => p.advised === "advised:noflip")).toMatchObject({ base: "noflip", unstamped: true, experimentId: null, attribution: "none" });
    expect(out.find((p) => p.advised === "advised:something-old")).toMatchObject({ base: "control", unstamped: true, experimentId: null, sessionsPaired: 1 });
  });

  it("looks up the stored underpowered verdict for the experiment the rows are stamped with", () => {
    const { config, tmp } = tmpConfig();
    seedStore(tmp, [{ id: "exp-1", module: "curve", base: "control", underpowered: true }]);
    const groups = [group("control", []), group("advised:control@exp-1", [])];
    const out = readAdvisedPairs(config, "curve", groups);
    expect(out[0]).toMatchObject({ base: "control", experimentId: "exp-1", underpowered: true, unstamped: false, attribution: "stamp" });
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
    expect(out[0]).toMatchObject({ experimentId: "exp-gone", experimentName: null, underpowered: null, unstamped: false, attribution: "stamp" });
  });

  it("splits earnings' strategy-suffixed tags without losing the strategy, under both namings", () => {
    const { config, tmp } = tmpConfig();
    seedStore(tmp, [{ id: "exp-2026-08-31-earnings-1", module: "earnings", base: "strat_test", name: "ironfly-take-earlier-than-condor" }]);
    const groups = [
      group("strat_test:iron_fly", ["2026-09-15"]),
      group("advised:strat_test:iron_fly@exp-2026-08-31-earnings-1", ["2026-09-15"]),
      group("advised:ironfly-take-earlier-than-condor:iron_fly", ["2026-09-15"]),
    ];
    const out = readAdvisedPairs(config, "earnings", groups);
    expect(out[0]).toMatchObject({ base: "strat_test:iron_fly", experimentId: "exp-2026-08-31-earnings-1", attribution: "stamp", sessionsPaired: 1 });
    expect(out[1]).toMatchObject({ base: "strat_test:iron_fly", experimentId: "exp-2026-08-31-earnings-1", attribution: "tag", sessionsPaired: 1 });
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
