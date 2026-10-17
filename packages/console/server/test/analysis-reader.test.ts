import { describe, it, expect, afterEach } from "vitest";
import path from "node:path";
import fs from "node:fs";
import os from "node:os";
import { ANALYSIS_VERSION, type ArmAnalysis } from "@console/shared";
import { readModuleAnalysis } from "../src/readers/analysis.js";
import { setAnalysisCaller, resetAnalysisCache, readAnalysis } from "../src/services/analysisBridge.js";
import type { ConsoleConfig } from "../src/config.js";

/**
 * `readModuleAnalysis` must read exactly what the performance slide reads (the same ledger file for
 * the mode, the same suite epoch, the same stamped era), and a refused read must say why rather
 * than return an empty arm list, which would read as "nothing traded".
 */

function fakeConfig(dataEpochDate: string | null): ConsoleConfig {
  const home = fs.mkdtempSync(path.join(os.tmpdir(), "console-analysis-test-"));
  const orchestratorConfig = path.join(home, "config.json");
  fs.writeFileSync(
    orchestratorConfig,
    JSON.stringify(dataEpochDate ? { data_epoch: { date: dataEpochDate, note: "advisor era" } } : {}),
  );
  const data = path.join(home, "data");
  const dir = (m: string) => path.join(data, m);
  return {
    port: 0,
    paths: {
      cherrypick: home,
      streamCacheDb: "",
      watchdogLast: "",
      orchestratorConfig,
      consoleData: "",
      meicDir: dir("meic"),
      fliesDir: dir("flies"),
      earningsDir: dir("earnings"),
      calendarsDir: dir("calendars"),
      pmccDir: dir("pmcc"),
      curveDir: dir("curve"),
      bwbDir: dir("bwb"),
      contangoDir: dir("contango"),
      gexDir: "",
      reviewDir: "",
      manageDir: "",
      overviewDir: "",
      advisorDir: "",
      adviceDir: "",
      meicRiskConfig: "",
      fliesConfig: "",
      pmccConfigCandidates: [],
      calendarsConfigCandidates: [],
      curveConfigCandidates: [],
    },
  };
}

const ARM = { version: ANALYSIS_VERSION, reading: { sample: 2 } } as unknown as ArmAnalysis;
const OK = { ok: true, error: null, analysis: { schema: "fly_book", n_records: 2, groups: { control: ARM } } };

afterEach(() => {
  setAnalysisCaller();
  resetAnalysisCache();
});

describe("readModuleAnalysis", () => {
  it("reads flies' paper ledger by default and its live ledger in live mode", () => {
    const seen: string[] = [];
    setAnalysisCaller((db) => {
      seen.push(db);
      return OK;
    });
    const config = fakeConfig(null);
    readModuleAnalysis(config, "flies", "current", "paper");
    readModuleAnalysis(config, "flies", "current", "live");
    expect(seen).toEqual([path.join(config.paths.fliesDir, "paper_trades.db"), path.join(config.paths.fliesDir, "live_trades.db")]);
  });

  it("passes the arms through by tag", () => {
    setAnalysisCaller(() => OK);
    const out = readModuleAnalysis(fakeConfig(null), "flies", "ALL");
    expect(out.ok).toBe(true);
    expect(out.nRecords).toBe(2);
    expect(out.groups).toEqual([{ tag: "control", analysis: ARM }]);
  });

  it("bounds era='current' to the suite epoch and pmcc to its stamped era, as performance does", () => {
    const seen: Array<[string, string | null, string | null]> = [];
    setAnalysisCaller((_db, schema, start, _end, era) => {
      seen.push([schema, start, era ?? null]);
      return OK;
    });
    const config = fakeConfig("2026-08-21");
    readModuleAnalysis(config, "flies", "current");
    readModuleAnalysis(config, "flies", "ALL");
    readModuleAnalysis(config, "pmcc", "current");
    expect(seen[0]).toEqual(["fly_book", "2026-08-21", null]);
    expect(seen[1]).toEqual(["fly_book", null, null]);
    expect(seen[2]?.[0]).toBe("pmcc");
    expect(seen[2]?.[2]).not.toBeNull();
  });

  it("refuses a live read for a module with no live ledger, without spawning", () => {
    let spawned = false;
    setAnalysisCaller(() => {
      spawned = true;
      return OK;
    });
    const out = readModuleAnalysis(fakeConfig(null), "curve", "current", "live");
    expect(out.ok).toBe(false);
    expect(out.error).toContain("no live ledger");
    expect(spawned).toBe(false);
  });

  it("reports a refused read as an error, not as no arms", () => {
    setAnalysisCaller(() => ({ ok: false, analysis: null, error: "cannot read" }));
    const out = readModuleAnalysis(fakeConfig(null), "flies");
    expect(out.ok).toBe(false);
    expect(out.error).toBe("cannot read");
    expect(out.groups).toEqual([]);
  });
});

describe("analysisBridge", () => {
  it("memoises a reading for the same ledger and window", () => {
    let calls = 0;
    setAnalysisCaller(() => {
      calls += 1;
      return OK;
    });
    readAnalysis("a.db", "fly_book", null, null, null, 1_000);
    readAnalysis("a.db", "fly_book", null, null, null, 2_000);
    readAnalysis("a.db", "fly_book", "2026-08-21", null, null, 2_000);
    expect(calls).toBe(2);
  });
});
