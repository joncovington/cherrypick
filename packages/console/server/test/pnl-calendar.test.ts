import { describe, it, expect, afterEach } from "vitest";
import path from "node:path";
import fs from "node:fs";
import os from "node:os";
import { readPnlCalendar, readPnlExcursion, readPnlTrades } from "../src/readers/pnlCalendar.js";
import { setExcursionCaller } from "../src/services/excursionBridge.js";
import { MODULE_ERA } from "../src/readers/performance.js";
import { setMetricsCaller, setTradesCaller, resetMetricsCache } from "../src/services/metricsBridge.js";
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
  setTradesCaller();
  setExcursionCaller();
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

  it("drills to one arm's trades over the calendar's own ledger, schema and era", async () => {
    const calls: unknown[][] = [];
    setTradesCaller((...args) => {
      calls.push(args);
      return Promise.resolve({ ok: true, trades: [], error: null });
    });
    const config = fakeConfig(null);
    const out = await readPnlTrades(config, "pmcc", "2026-10-13", "shield", "paper");
    expect(out).toMatchObject({ ok: true, module: "pmcc", session: "2026-10-13", tag: "shield", mode: "paper" });
    expect(calls).toHaveLength(1);
    const [db, schema, session, tag, era] = calls[0] as [string, string, string, string, string | null];
    expect(db).toContain(`${path.sep}pmcc${path.sep}`);
    expect([schema, session, tag, era]).toEqual(["pmcc", "2026-10-13", "shield", MODULE_ERA.pmcc]);
  });

  it("refuses a live drill on a module that keeps no live ledger, without spawning", async () => {
    let spawned = false;
    setTradesCaller(() => {
      spawned = true;
      return Promise.resolve({ ok: true, trades: [], error: null });
    });
    const out = await readPnlTrades(fakeConfig(null), "curve", "2026-10-13", "control", "live");
    expect(out.ok).toBe(false);
    expect(spawned).toBe(false);
  });

  it("reads a position's excursion from the same ledger and schema as its trades", async () => {
    const calls: unknown[][] = [];
    setExcursionCaller((...args) => {
      calls.push(args);
      return Promise.resolve({ ok: true, recorded: true, basis: "gross_at_mid" });
    });
    const out = await readPnlExcursion(fakeConfig(null), "bwb", "SPX:control:2026-10-15", "live");
    expect(out).toMatchObject({ ok: true, basis: "gross_at_mid" });
    const [db, schema, position] = calls[0] as [string, string, string];
    expect(db).toContain(`${path.sep}bwb${path.sep}live_trades.db`);
    expect([schema, position]).toEqual(["bwb_132", "SPX:control:2026-10-15"]);
  });

  it("reads only the modules with a live ledger in live mode", async () => {
    setMetricsCaller(() => reading([["2026-10-13", 1]]));
    const out = await readPnlCalendar(fakeConfig(null), "live");
    expect(new Set(out.days.map((d) => d.module))).toEqual(new Set(["meic", "flies", "earnings", "bwb"]));
  });
});
