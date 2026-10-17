import { describe, it, expect, afterEach } from "vitest";
import path from "node:path";
import fs from "node:fs";
import os from "node:os";
import { readPnlCalendar } from "../src/readers/pnlCalendar.js";
import { setMetricsCaller, resetMetricsCache } from "../src/services/metricsBridge.js";
import type { ConsoleConfig } from "../src/config.js";

/** Compare's calendar reads every module's per-session nets (core.metrics) over the suite window. */

function fakeConfig(dataEpochDate: string | null): ConsoleConfig {
  const home = fs.mkdtempSync(path.join(os.tmpdir(), "console-calendar-test-"));
  const orchestratorConfig = path.join(home, "config.json");
  fs.writeFileSync(orchestratorConfig, JSON.stringify(dataEpochDate ? { data_epoch: { date: dataEpochDate, note: "n" } } : {}));
  const dir = (m: string) => path.join(home, "data", m);
  return {
    port: 0,
    paths: {
      cherrypick: home, streamCacheDb: "", watchdogLast: "", orchestratorConfig, consoleData: "",
      meicDir: dir("meic"), fliesDir: dir("flies"), earningsDir: dir("earnings"), calendarsDir: dir("calendars"),
      pmccDir: dir("pmcc"), curveDir: dir("curve"), bwbDir: dir("bwb"), contangoDir: dir("contango"),
      gexDir: "", reviewDir: "", manageDir: "", overviewDir: "", advisorDir: "", adviceDir: "", meicRiskConfig: "",
      fliesConfig: "", pmccConfigCandidates: [], calendarsConfigCandidates: [], curveConfigCandidates: [],
    },
  };  // fmt: skip
}

const reading = (nets: Array<[string, number]>) => ({
  ok: true,
  error: null,
  metrics: { schema: "x", n_records: 1, groups: { control: { reading: {}, session_nets: nets, trade_nets: [] } } },
});

afterEach(() => {
  setMetricsCaller();
  resetMetricsCache();
});

describe("readPnlCalendar", () => {
  it("lists every module's arm-sessions over the suite window, and names a failed module", async () => {
    const starts: Array<string | null> = [];
    setMetricsCaller((db, _schema, start) => {
      starts.push(start);
      if (db.includes(`${path.sep}curve${path.sep}`)) return { ok: false, metrics: null, error: "cannot read" };
      return reading([["2026-10-13", 120]]);
    });
    const out = await readPnlCalendar(fakeConfig("2026-08-21"), "paper");
    expect(out.from).toBe("2026-08-21");
    expect(new Set(starts)).toEqual(new Set(["2026-08-21"]));
    expect(out.failures).toEqual([{ module: "curve", error: "cannot read" }]);
    expect(out.days.filter((d) => d.module === "flies")).toEqual([
      { module: "flies", tag: "control", session: "2026-10-13", net: 120 },
    ]);
  });

  it("reads only the modules with a live ledger in live mode", async () => {
    setMetricsCaller(() => reading([["2026-10-13", 1]]));
    const out = await readPnlCalendar(fakeConfig(null), "live");
    expect(new Set(out.days.map((d) => d.module))).toEqual(new Set(["meic", "flies", "earnings", "bwb"]));
  });
});
