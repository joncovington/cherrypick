import path from "node:path";
import {
  PERFORMANCE_MODULE_SCHEMA,
  type ExitReasonRow,
  type HeldBackRow,
  type AdvisedPair,
  type MeasurementBreak,
  type ExcursionsResult,
  type ModulePerformanceGroup,
  type ModulePerformanceResult,
  type PerformanceModuleId,
} from "@console/shared";
import type { ConsoleConfig } from "../config.js";
import { suiteEra, withReadOnlyDb } from "./db.js";
import { readModuleMetrics, type ModuleMetricsGroup } from "../services/metricsBridge.js";
import { readExitReasons } from "./exitReasons.js";
import { readAdvisedPairs } from "./pairs.js";
import { readMeasurementBreaks } from "./integrity.js";
import { readExcursions } from "../services/excursionsBridge.js";

/**
 * The shared performance read: one module's calibration reading, per profile, via
 * `services/metricsBridge.ts`. The counterpart to each module's own bespoke reader
 * (`readers/meic.ts`, `readers/curve.ts`, ...) -- this is the SUITE-WIDE half every module shares
 * (the same ~20 metrics, the same schema registry), not a replacement for a module's own richer
 * page.
 *
 * Every module here trades paper as its evidence source (`calibrate`'s own "paper only" rule --
 * live-tagged ledgers never feed a promotion reading), so the db path is always that module's
 * `paper_trades.db`.
 */

export const MODULE_SCHEMA = PERFORMANCE_MODULE_SCHEMA;

export type { PerformanceModuleId };

const MODULE_DIR_KEY = {
  meic: "meicDir",
  flies: "fliesDir",
  earnings: "earningsDir",
  calendars: "calendarsDir",
  pmcc: "pmccDir",
  curve: "curveDir",
  bwb: "bwbDir",
} as const satisfies Record<PerformanceModuleId, keyof ConsoleConfig["paths"]>;

export function performanceDbPath(config: ConsoleConfig, module: PerformanceModuleId): string {
  return path.join(config.paths[MODULE_DIR_KEY[module]], "paper_trades.db");
}

function readBreaks(dbPath: string): MeasurementBreak[] {
  return withReadOnlyDb<MeasurementBreak[]>(dbPath, [], (db) => readMeasurementBreaks(db));
}

/**
 * `era="current"` (the default) bounds to the suite's own `data_epoch` (`suiteEra` -- the same
 * lever `calibrate` enforces); `era="ALL"` pools every session on file. This is deliberately the
 * SUITE-WIDE epoch only: a module with its own finer era table (MEIC's advisor-era cutover,
 * pmcc's 2026-08-23 redesign, stored as a ledger column rather than a date `core.metrics` can
 * bound on) is not yet integrated here -- widening `core.metrics read` to accept an era filter
 * directly (rather than only `--start`/`--end`) is a follow-up, not silently approximated by a
 * date guess that could disagree with the module's own boundary.
 */
export function readModulePerformance(
  config: ConsoleConfig,
  module: PerformanceModuleId,
  era: "current" | "ALL" = "current",
): ModulePerformanceResult {
  const schema = MODULE_SCHEMA[module];
  const dbPath = performanceDbPath(config, module);
  const suite = suiteEra(config.paths.orchestratorConfig);
  const start = era === "current" ? suite.from : null;

  // Independent of the metrics reading -- a query straight off the ledger, not through
  // metricsBridge -- so it's read whether or not the calibration reading itself succeeds; a
  // module whose ledger schema `core.metrics` doesn't yet know should still show its exit reasons.
  const exits = readExitReasons(config, module);
  const breaks = readBreaks(dbPath);
  const excursions = readExcursions(module, dbPath);

  const res = readModuleMetrics(dbPath, schema, start, null);
  if (!res.ok || res.metrics === null) {
    return {
      ok: false,
      module,
      schema,
      era: { key: era, from: start, note: suite.note },
      nRecords: 0,
      groups: [],
      exitReasons: exits.exitReasons,
      heldBack: exits.heldBack,
      pairs: [],
      breaks,
      excursions,
      error: res.error,
    };
  }
  const groups = Object.entries(res.metrics.groups).map(([tag, g]: [string, ModuleMetricsGroup]) => ({
    tag,
    reading: g.reading,
    sessionNets: g.session_nets,
    tradeNets: g.trade_nets,
  }));
  return {
    ok: true,
    module,
    schema,
    era: { key: era, from: start, note: suite.note },
    nRecords: res.metrics.n_records,
    groups,
    exitReasons: exits.exitReasons,
    heldBack: exits.heldBack,
    pairs: readAdvisedPairs(config, module, groups),
    breaks,
    excursions,
    error: null,
  };
}
