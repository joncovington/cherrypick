import { describe, it, expect, beforeEach, afterEach } from "vitest";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import Database from "better-sqlite3";
import type { ConsoleConfig } from "../src/config.js";
import { listRegimeCutSessions, readRegimeCuts, shapeRegimeCuts } from "../src/readers/regimeCuts.js";
import { closePooledDbs } from "../src/readers/db.js";

/**
 * The regime-cuts reader. What is pinned: absent, malformed and stale are three different
 * payloads; `thin` is the writer's flag and is never re-derived here; the dimension list is the
 * one thing derived, from the data, in first-seen order; and the meic path reads its own dir and
 * its own ledger table for the staleness check.
 */

let tmp: string;
let config: ConsoleConfig;

function cfg(root: string): ConsoleConfig {
  fs.mkdirSync(path.join(root, "flies"), { recursive: true });
  fs.mkdirSync(path.join(root, "meic"), { recursive: true });
  return {
    port: 0,
    paths: {
      cherrypick: root,
      meicDir: path.join(root, "meic"),
      fliesDir: path.join(root, "flies"),
    },
  } as unknown as ConsoleConfig;
}

/**
 * The same document as cut_version 1 spelled it: `arms`/`arm`/`arm_column` were
 * `books`/`book`/`book_column`. The dated per-session artifacts the picker lists are never
 * rewritten, so this shape stays readable for good.
 */
function asCutV1(doc: Record<string, unknown>): Record<string, unknown> {
  const ren = (o: unknown) => {
    const r = { ...(o as Record<string, unknown>) };
    if ("arm" in r) { r["book"] = r["arm"]; delete r["arm"]; }
    return r;
  };
  const out: Record<string, unknown> = { ...doc, cut_version: 1 };
  out["books"] = ((doc["arms"] as unknown[]) ?? []).map(ren);
  delete out["arms"];
  out["cross_tabs"] = ((doc["cross_tabs"] as Record<string, unknown>[]) ?? []).map((t) => ({
    ...t,
    books: ((t["arms"] as unknown[]) ?? []).map(ren),
    arms: undefined,
  }));
  out["book_column"] = doc["arm_column"];
  delete out["arm_column"];
  return out;
}

function minimal(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    cut_version: 2,
    module: "flies",
    generated_at: "2026-09-18T16:40:00-04:00",
    session: "2026-09-18",
    symbol: "SPX",
    arm_column: "arm",
    entry_modes: ["legged"],
    phase: "entry",
    thin_below_sessions: 3,
    min_effective_n: 14,
    era: {
      start: "2026-08-21",
      bounding_break: { break_date: "2026-08-21", scope: "*", kind: "cutover", reason: "era" },
      breaks: [{ break_date: "2026-08-21", scope: "*", kind: "cutover", reason: "era", binding: true }],
      ignored_future: [{ break_date: "2026-12-18", scope: "*", kind: "entry_rules" }],
      caveats: [],
    },
    arms: [
      {
        arm: "control",
        era_start: "2026-08-21",
        era_break: null,
        sessions: 19,
        trades: 128,
        net_pnl: 3306.69,
        win_rate: 0.8,
        completed: 102,
        completion_rate: 0.7969,
        dimensions: {
          gex: {
            coverage_pct: 100,
            tagged: 128,
            untagged: 0,
            sessions: 19,
            effective_n: 19,
            daily_scale: true,
            degenerate: false,
            underpowered: false,
            buckets: [
              { bucket: "diffuse", value_min: 0.1, value_max: 0.5, sessions: 1, trades: 70, net_pnl: 861, thin: false },
              { bucket: "clustered", sessions: 12, trades: 42, net_pnl: 1064, thin: false },
            ],
          },
          trend: { coverage_pct: 100, tagged: 128, untagged: 0, sessions: 19, effective_n: 19, daily_scale: true, degenerate: false, underpowered: false, buckets: [] },
        },
      },
      {
        arm: "callwall",
        era_start: "2026-08-31",
        era_break: { break_date: "2026-08-31", scope: "callwall", kind: "arm_added", reason: "added" },
        sessions: 12,
        trades: 22,
        net_pnl: -299.83,
        win_rate: 0.6,
        completed: 17,
        completion_rate: 0.77,
        dimensions: {
          vol: { coverage_pct: 50, tagged: 11, untagged: 11, sessions: 6, effective_n: 6, daily_scale: true, degenerate: false, underpowered: true, buckets: [] },
        },
      },
    ],
    cross_tabs: [
      {
        dims: ["gex", "trend"],
        arms: [{ book: "control", cells: [{ buckets: ["diffuse", "up_from_open"], sessions: 7, trades: 21, net_pnl: -898.79, thin: false }] }],
      },
      {
        dims: ["gex", "drift_alignment"],
        arms: [{ book: "control", cells: [{ buckets: ["diffuse", "against"], sessions: 6, trades: 14, net_pnl: -240.0, thin: false }] }],
      },
    ],
    ...overrides,
  };
}

function write(module: "flies" | "meic", name: string, doc: unknown): void {
  fs.writeFileSync(path.join(tmp, module, name), typeof doc === "string" ? doc : JSON.stringify(doc));
}

function ledger(module: "flies" | "meic", table: string, latest: string): void {
  const db = new Database(path.join(tmp, module, "paper_trades.db"));
  db.exec(`CREATE TABLE ${table} (trade_date TEXT, status TEXT)`);
  db.prepare(`INSERT INTO ${table} VALUES (?, 'settled')`).run(latest);
  db.close();
}

beforeEach(() => {
  tmp = fs.mkdtempSync(path.join(os.tmpdir(), "console-regime-"));
  config = cfg(tmp);
});

afterEach(() => {
  closePooledDbs();
});

describe("readRegimeCuts", () => {
  it("an absent artifact is status absent, never an empty table", () => {
    const out = readRegimeCuts(config, "flies");
    expect(out.status).toBe("absent");
    if (out.status === "absent") expect(out.path.endsWith("regime_cuts.json")).toBe(true);
  });

  it("unparseable JSON is status failed and carries the error", () => {
    write("flies", "regime_cuts.json", "{not json");
    const out = readRegimeCuts(config, "flies");
    expect(out.status).toBe("failed");
    if (out.status === "failed") expect(out.error).toMatch(/unparseable/);
  });

  it("a wrong cut_version is failed, not silently rendered", () => {
    write("flies", "regime_cuts.json", minimal({ cut_version: 3 }));
    const out = readRegimeCuts(config, "flies");
    expect(out.status).toBe("failed");
    if (out.status === "failed") expect(out.error).toMatch(/cut_version 3/);
  });

  it("a valid artifact passes thin through untouched and never re-derives it", () => {
    // The diffuse bucket has ONE session and thin:false -- an impossible pair the writer would
    // never emit, put there so a reader that recomputed `sessions < 3` would flip it and fail.
    write("flies", "regime_cuts.json", minimal());
    const out = readRegimeCuts(config, "flies");
    expect(out.status).toBe("ok");
    if (out.status !== "ok") return;
    const diffuse = out.cuts.arms[0]!.dimensions["gex"]!.buckets.find((b) => b.bucket === "diffuse")!;
    expect(diffuse.sessions).toBe(1);
    expect(diffuse.thin).toBe(false);
    expect(out.cuts.crossTabs[0]!.arms[0]!.cells[0]!.thin).toBe(false);
  });

  it("passes an arm's thin through, and never invents the writer's thresholds", () => {
    // `thin: false` on a 1-session arm is impossible from the writer -- there so a reader that
    // re-derived `sessions < thinBelowSessions` would flip it. And an artifact publishing no
    // thresholds reads null, not a console copy of core's 3 and 14.
    const doc = minimal() as Record<string, unknown> & { arms: Array<Record<string, unknown>> };
    doc.arms[0] = { ...doc.arms[0], sessions: 1, thin: false };
    delete doc["thin_below_sessions"];
    delete doc["min_effective_n"];
    write("flies", "regime_cuts.json", doc);
    const out = readRegimeCuts(config, "flies");
    expect(out.status).toBe("ok");
    if (out.status !== "ok") return;
    expect(out.cuts.arms[0]!.thin).toBe(false);
    expect(out.cuts.arms[1]!.thin).toBeNull(); // written before arm-level stamping
    expect(out.cuts.thinBelowSessions).toBeNull();
    expect(out.cuts.minEffectiveN).toBeNull();
  });

  it("carries every declared cross-tab, in the writer's order", () => {
    // A regression pin, not a red-first test: the reader already mapped them all, but the slide
    // took [0] until 2026-09-22 and flies now declares two.
    write("flies", "regime_cuts.json", minimal());
    const out = readRegimeCuts(config, "flies");
    expect(out.status).toBe("ok");
    if (out.status !== "ok") return;
    expect(out.cuts.crossTabs.map((t) => t.dims)).toEqual([
      ["gex", "trend"],
      ["gex", "drift_alignment"],
    ]);
  });

  it("the dimension list is derived from the data in first-seen order", () => {
    write("flies", "regime_cuts.json", minimal());
    const out = readRegimeCuts(config, "flies");
    if (out.status !== "ok") throw new Error(out.status);
    expect(out.cuts.dimensions).toEqual(["gex", "trend", "vol"]);
    expect(out.cuts.era.ignoredFuture.map((b) => b.breakDate)).toEqual(["2026-12-18"]);
    expect(out.cuts.arms[1]!.eraBreak?.kind).toBe("arm_added");
  });

  it("a ledger newer than the artifact is reported as stale; no ledger means null", () => {
    write("flies", "regime_cuts.json", minimal());
    let out = readRegimeCuts(config, "flies");
    if (out.status !== "ok") throw new Error(out.status);
    expect(out.stale).toBeNull();
    ledger("flies", "fly_positions", "2026-09-19");
    out = readRegimeCuts(config, "flies");
    if (out.status !== "ok") throw new Error(out.status);
    expect(out.stale).toEqual({ artifactSession: "2026-09-18", latestSession: "2026-09-19" });
  });

  it("the meic path reads its own dir and its own ledger table", () => {
    write("meic", "regime_cuts.json", minimal({ module: "meic", book_column: "risk_profile", entry_modes: null }));
    ledger("meic", "ic_trades", "2026-09-18");
    const out = readRegimeCuts(config, "meic");
    if (out.status !== "ok") throw new Error(out.status);
    expect(out.cuts.module).toBe("meic");
    expect(out.cuts.entryModes).toBeNull();
    expect(out.stale).toBeNull();
    expect(readRegimeCuts(config, "flies").status).toBe("absent");
  });

  /**
   * cut_version 2 renamed `books`/`book`/`book_column` to `arms`/`arm`/`arm_column`. The module
   * rewrites `regime_cuts.json` nightly so it reaches 2 within a day, but the dated per-session
   * copies the picker lists are never rewritten and stay at 1 for good.
   *
   * Comparing the two whole shaped objects is the point: it fails on any divergence, not just the
   * ones named here. A reader that took only `arms` would not throw on a v1 artifact -- it would
   * fail the shape check and render "unexpected shape" over a file that is perfectly good.
   */
  it("shapes a cut_version 1 artifact exactly like the version 2 one", () => {
    write("flies", "regime_cuts.json", minimal());
    const v2 = readRegimeCuts(config, "flies");

    write("flies", "regime_cuts.json", asCutV1(minimal()));
    const v1 = readRegimeCuts(config, "flies");

    expect(v1.status).toBe("ok");
    if (v1.status !== "ok" || v2.status !== "ok") return;
    expect({ ...v1.cuts, cutVersion: 0 }).toEqual({ ...v2.cuts, cutVersion: 0 });
    expect(v1.cuts.arms[0]!.arm).toBe("control");
    expect(v1.cuts.armColumn).toBe("arm");
  });

  /**
   * The 2026-09-28 robustness stamps are additive (no cut_version bump), so both halves are pinned:
   * a stamped artifact maps every key, and one written before them reads null / [] -- never a
   * derived value, and never `fragile: false`, which would read as "checked and found sound".
   */
  it("maps the writer's robustness, history, paired and multiplicity stamps", () => {
    const doc = minimal() as Record<string, unknown> & { arms: Array<Record<string, unknown>> };
    const gex = (doc.arms[0]!["dimensions"] as Record<string, Record<string, unknown>>)["gex"]!;
    const buckets = gex["buckets"] as Array<Record<string, unknown>>;
    // fragile:true beside a 0.2 share and no sign flips is a pair the writer's own rule never emits --
    // there so a reader that re-derived fragility from the robustness numbers would read false.
    buckets[1] = {
      ...buckets[1],
      fragile: true,
      robustness: {
        positive_sessions: 7,
        largest_session_net: -412.5,
        largest_session_share: 0.2,
        sign_flips_dropping_one: 0,
        net_interval: [-150.25, 2200.0],
        interval_level: 0.9,
        interval_excludes_zero: false,
      },
      history: { snapshots: 6, first_net: -300.0, sign_changes: 2 },
    };
    gex["paired"] = [
      {
        a: "diffuse",
        b: "clustered",
        sessions: 8,
        a_better_sessions: 6,
        b_better_sessions: 2,
        mean_diff_per_trade: 12.5,
        median_diff_per_trade: 9.75,
        sign_test_p: 0.2891,
      },
    ];
    const tabs = doc["cross_tabs"] as Array<{ arms: Array<{ cells: Array<Record<string, unknown>> }> }>;
    tabs[0]!.arms[0]!.cells[0] = {
      ...tabs[0]!.arms[0]!.cells[0],
      fragile: false,
      robustness: { positive_sessions: 4, largest_session_net: 100, largest_session_share: 0.1, sign_flips_dropping_one: 0, net_interval: [5, 50], interval_level: 0.9, interval_excludes_zero: true },
      history: { snapshots: 0, first_net: null, sign_changes: 0 },
    };
    doc["multiplicity"] = {
      alpha: 0.1,
      // Distinct from alpha on purpose: a reader that borrowed the interval bar would fail.
      paired_alpha: 0.05,
      intervals: 81,
      intervals_excluding_zero: 36,
      intervals_expected_by_chance: 8.1,
      paired_tests: 32,
      paired_below_alpha: 6,
      paired_expected_by_chance: 3.2,
    };
    doc["history"] = { window: 10, sessions: ["2026-09-25", "2026-09-26"] };
    write("flies", "regime_cuts.json", doc);

    const out = readRegimeCuts(config, "flies");
    if (out.status !== "ok") throw new Error(out.status);
    const d = out.cuts.arms[0]!.dimensions["gex"]!;
    const clustered = d.buckets.find((b) => b.bucket === "clustered")!;
    expect(clustered.fragile).toBe(true);
    expect(clustered.robustness).toEqual({
      positiveSessions: 7,
      largestSessionNet: -412.5,
      largestSessionShare: 0.2,
      signFlipsDroppingOne: 0,
      netInterval: [-150.25, 2200.0],
      intervalLevel: 0.9,
      intervalExcludesZero: false,
    });
    expect(clustered.history).toEqual({ snapshots: 6, firstNet: -300.0, signChanges: 2 });
    expect(d.paired).toEqual([
      {
        a: "diffuse",
        b: "clustered",
        sessions: 8,
        aBetterSessions: 6,
        bBetterSessions: 2,
        meanDiffPerTrade: 12.5,
        medianDiffPerTrade: 9.75,
        signTestP: 0.2891,
      },
    ]);
    const cross = out.cuts.crossTabs[0]!.arms[0]!.cells[0]!;
    expect(cross.fragile).toBe(false);
    expect(cross.robustness?.netInterval).toEqual([5, 50]);
    expect(cross.robustness?.intervalExcludesZero).toBe(true);
    expect(cross.history).toEqual({ snapshots: 0, firstNet: null, signChanges: 0 });
    expect(out.cuts.multiplicity).toEqual({
      alpha: 0.1,
      pairedAlpha: 0.05,
      intervals: 81,
      intervalsExcludingZero: 36,
      intervalsExpectedByChance: 8.1,
      pairedTests: 32,
      pairedBelowAlpha: 6,
      pairedExpectedByChance: 3.2,
    });
    expect(out.cuts.history).toEqual({ window: 10, sessions: ["2026-09-25", "2026-09-26"] });
  });

  it("an artifact written before the stamps reads null and [], never a derived value", () => {
    write("flies", "regime_cuts.json", minimal());
    const out = readRegimeCuts(config, "flies");
    if (out.status !== "ok") throw new Error(out.status);
    const d = out.cuts.arms[0]!.dimensions["gex"]!;
    for (const b of d.buckets) {
      expect(b.fragile).toBeNull();
      expect(b.robustness).toBeNull();
      expect(b.history).toBeNull();
    }
    expect(d.paired).toEqual([]);
    const cross = out.cuts.crossTabs[0]!.arms[0]!.cells[0]!;
    expect([cross.fragile, cross.robustness, cross.history]).toEqual([null, null, null]);
    expect(out.cuts.multiplicity).toBeNull();
    expect(out.cuts.history).toBeNull();
  });

  it("dated artifacts are listed and selectable by session", () => {
    write("flies", "regime_cuts-2026-09-17.json", minimal({ session: "2026-09-17" }));
    write("flies", "regime_cuts-2026-09-18.json", minimal());
    write("flies", "regime_cuts.json", minimal());
    expect(listRegimeCutSessions(config, "flies")).toEqual(["2026-09-17", "2026-09-18"]);
    const out = readRegimeCuts(config, "flies", "2026-09-17");
    if (out.status !== "ok") throw new Error(out.status);
    expect(out.cuts.session).toBe("2026-09-17");
    expect(readRegimeCuts(config, "flies", "2026-09-10").status).toBe("absent");
  });
});

describe("shapeRegimeCuts", () => {
  it("tolerates a missing optional block without throwing", () => {
    const cuts = shapeRegimeCuts({ cut_version: 2, arms: [] });
    expect(cuts.arms).toEqual([]);
    expect(cuts.dimensions).toEqual([]);
    expect(cuts.era.start).toBeNull();
  });
});
