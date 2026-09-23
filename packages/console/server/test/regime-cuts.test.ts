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
