import { describe, it, expect, afterEach } from "vitest";
import path from "node:path";
import fs from "node:fs";
import os from "node:os";
import Database from "better-sqlite3";
import {
  readModulePerformance,
  performanceDbPath,
  MODULE_SCHEMA,
} from "../src/readers/performance.js";
import { setMetricsCaller, resetMetricsCache } from "../src/services/metricsBridge.js";
import type { ConsoleConfig } from "../src/config.js";

/**
 * `readModulePerformance` ties a module id to its schema and paper-ledger path, then reads
 * through `metricsBridge`. What's worth pinning here: the module->schema->dbPath mapping (a wrong
 * entry would silently read the wrong module's ledger), that era="current" bounds to the suite
 * epoch while era="ALL" pools everything, and that a refused read reports the error rather than
 * an empty groups array (which would read as "no profiles traded").
 */

function fakeConfig(dataEpochDate: string | null): ConsoleConfig {
  const home = fs.mkdtempSync(path.join(os.tmpdir(), "console-perf-test-"));
  const orchestratorConfig = path.join(home, "config.json");
  fs.writeFileSync(
    orchestratorConfig,
    JSON.stringify(dataEpochDate ? { data_epoch: { date: dataEpochDate, note: "advisor era" } } : {}),
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
    },
  };
}

afterEach(() => {
  setMetricsCaller();
  resetMetricsCache();
});

const READING = {
  ok: true,
  error: null,
  metrics: {
    schema: "curve_vx",
    n_records: 3,
    groups: {
      control: {
        reading: { sample: 3, net_pnl: 12.5 },
        session_nets: [["2026-08-20", 12.5]] as Array<[string, number]>,
        trade_nets: [4.0, 4.0, 4.5],
      },
    },
  },
};

describe("performance db path resolution", () => {
  it("maps every module to its own dir and paper_trades.db", () => {
    const config = fakeConfig(null);
    for (const module of Object.keys(MODULE_SCHEMA) as Array<keyof typeof MODULE_SCHEMA>) {
      const p = performanceDbPath(config, module);
      expect(p.endsWith(path.join(module === "meic" ? "meic" : module, "paper_trades.db"))).toBe(true);
    }
  });

  it("uses the schema each module's ledger actually declares", () => {
    expect(MODULE_SCHEMA.meic).toBe("meic_ic");
    expect(MODULE_SCHEMA.flies).toBe("fly_book");
    expect(MODULE_SCHEMA.earnings).toBe("earnings");
    expect(MODULE_SCHEMA.calendars).toBe("dc_week");
    expect(MODULE_SCHEMA.pmcc).toBe("pmcc_99");
    expect(MODULE_SCHEMA.curve).toBe("curve_vx");
    expect(MODULE_SCHEMA.bwb).toBe("bwb_132");
  });
});

describe("readModulePerformance", () => {
  it("passes the reading through, tagged by profile", () => {
    setMetricsCaller(() => READING);
    const out = readModulePerformance(fakeConfig(null), "curve", "ALL");
    expect(out.ok).toBe(true);
    expect(out.nRecords).toBe(3);
    expect(out.groups).toEqual([
      {
        tag: "control",
        reading: { sample: 3, net_pnl: 12.5 },
        sessionNets: [["2026-08-20", 12.5]],
        tradeNets: [4.0, 4.0, 4.5],
      },
    ]);
  });

  it("era='current' bounds start to the suite's own data_epoch date", () => {
    const seen: Array<[string | null, string | null]> = [];
    setMetricsCaller((_db, _schema, start, end) => {
      seen.push([start, end]);
      return READING;
    });
    readModulePerformance(fakeConfig("2026-08-21"), "curve", "current");
    expect(seen).toEqual([["2026-08-21", null]]);
  });

  it("era='ALL' passes no date bound at all", () => {
    const seen: Array<[string | null, string | null]> = [];
    setMetricsCaller((_db, _schema, start, end) => {
      seen.push([start, end]);
      return READING;
    });
    readModulePerformance(fakeConfig("2026-08-21"), "curve", "ALL");
    expect(seen).toEqual([[null, null]]);
  });

  it("era='current' with no declared data_epoch reads everything, not nothing", () => {
    const seen: Array<[string | null, string | null]> = [];
    setMetricsCaller((_db, _schema, start, end) => {
      seen.push([start, end]);
      return READING;
    });
    readModulePerformance(fakeConfig(null), "curve", "current");
    expect(seen).toEqual([[null, null]]);
  });

  it("reports a refused read as an error, never an empty groups array", () => {
    setMetricsCaller(() => ({ ok: false, metrics: null, error: "unknown schema" }));
    const out = readModulePerformance(fakeConfig(null), "curve", "ALL");
    expect(out.ok).toBe(false);
    expect(out.groups).toEqual([]);
    expect(out.error).toBe("unknown schema");
  });

  it("carries exitReasons/heldBack from the ledger even when the metrics reading fails", () => {
    // exitReasons reads straight off the ledger (readExitReasons), independent of metricsBridge --
    // a module whose schema core.metrics doesn't yet recognise should still show its exit reasons.
    const config = fakeConfig(null);
    fs.mkdirSync(config.paths.curveDir, { recursive: true });
    const db = new Database(path.join(config.paths.curveDir, "paper_trades.db"));
    db.exec(
      "CREATE TABLE curve_positions (position_id TEXT, book TEXT, status TEXT, exit_reason TEXT, gross_pnl REAL, fees REAL)",
    );
    db.prepare(
      "INSERT INTO curve_positions VALUES ('p1','control','closed','profit_take',40.0,2.0)",
    ).run();
    db.close();

    setMetricsCaller(() => ({ ok: false, metrics: null, error: "unavailable" }));
    const out = readModulePerformance(config, "curve", "ALL");
    expect(out.ok).toBe(false); // the metrics half still failed
    expect(out.exitReasons).toEqual([
      { tag: "control", reason: "profit_take", n: 1, net: 38.0, avgNet: 38.0 },
    ]);
    expect(out.heldBack).toEqual([]);
  });
});
