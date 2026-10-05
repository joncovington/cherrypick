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
  type TradingMode,
} from "@console/shared";
import type { ConsoleConfig } from "../config.js";
import { suiteEra, withReadOnlyDb } from "./db.js";
import { readModuleMetrics, type ModuleMetricsGroup } from "../services/metricsBridge.js";
import { readExitReasons } from "./exitReasons.js";
import { readAdvisedPairs } from "./pairs.js";
import { readMeasurementBreaks } from "./integrity.js";
import { readExcursions } from "../services/excursionsBridge.js";
import { onPeakRiskOver, peakRiskSessions } from "./fliesPeakRisk.js";
import { CURRENT_ERA as PMCC_CURRENT_ERA } from "./pmcc.js";

/**
 * A module whose ledger stamps its own era on every row, and the era it counts as evidence. The
 * suite epoch alone is not that module's window: pmcc's eras are roster changes stamped at entry,
 * so a date bound would both admit earlier eras' rows and disagree with the module's own headline.
 * Scoped by the stamp, through `core.metrics read --era` (`ledgers.ERA_SCHEMAS`).
 */
const MODULE_ERA: Partial<Record<PerformanceModuleId, string>> = { pmcc: PMCC_CURRENT_ERA };

/**
 * The shared performance read: one module's calibration reading, per profile, via
 * `services/metricsBridge.ts`. The counterpart to each module's own bespoke reader
 * (`readers/meic.ts`, `readers/curve.ts`, ...) -- this is the SUITE-WIDE half every module shares
 * (the same ~20 metrics, the same schema registry), not a replacement for a module's own richer
 * page.
 *
 * Every module here trades paper as its evidence source (`calibrate`'s own "paper only" rule --
 * live-tagged ledgers never feed a promotion reading), so the default read is that module's
 * `paper_trades.db`. `mode="live"` reads the live ledger instead, for the modules whose page has a
 * paper/live toggle: until 2026-09-30 this slide ignored the toggle and showed paper under a live
 * badge. A live reading is a measurement of the live book, never a promotion input, and carries no
 * advised pairs -- advised books exist only on paper.
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

/**
 * The live ledger of each module whose page carries a paper/live toggle -- the same file that
 * module's own readers open in live mode. A module absent here has no live book to read.
 */
export const LIVE_LEDGER = {
  meic: "meic_trades.db",
  flies: "live_trades.db",
  earnings: "earnings_trades.db",
} as const satisfies Partial<Record<PerformanceModuleId, string>>;

function hasLiveLedger(module: PerformanceModuleId): module is keyof typeof LIVE_LEDGER {
  return module in LIVE_LEDGER;
}

/** The ledger file for `mode`, or null when the module has no live book. */
export function ledgerFile(module: PerformanceModuleId, mode: TradingMode): string | null {
  if (mode === "paper") return "paper_trades.db";
  return hasLiveLedger(module) ? LIVE_LEDGER[module] : null;
}

/** A calibration group's tag back to its rows: `core.metrics` names a group `<arm>@<experiment id>`
 *  for rows stamped with an experiment and `<arm>` for the rest. */
export function flyTagScope(tag: string): { arm: string; experimentId: string | null } {
  const at = tag.indexOf("@");
  return at < 0 ? { arm: tag, experimentId: null } : { arm: tag.slice(0, at), experimentId: tag.slice(at + 1) };
}

function readBreaks(dbPath: string): MeasurementBreak[] {
  return withReadOnlyDb<MeasurementBreak[]>(dbPath, [], (db) => readMeasurementBreaks(db));
}

/**
 * `era="current"` (the default) bounds to the suite's own `data_epoch` (`suiteEra` -- the same
 * lever `calibrate` enforces), and for a module in `MODULE_ERA` also to its own stamped era
 * (`core.metrics read --era`, never a date guess that could disagree with the module's boundary).
 * The exit-reason, held-back and excursion reads take the same era, so no card on the slide answers
 * for a different window. `era="ALL"` pools every session and every era on file. MEIC's
 * advisor-era cutover is a date, not a stamp, and is not integrated here yet.
 */
export function readModulePerformance(
  config: ConsoleConfig,
  module: PerformanceModuleId,
  era: "current" | "ALL" = "current",
  mode: TradingMode = "paper",
): ModulePerformanceResult {
  const schema = MODULE_SCHEMA[module];
  const suite = suiteEra(config.paths.orchestratorConfig);
  const start = era === "current" ? suite.from : null;
  const moduleEra = era === "current" ? (MODULE_ERA[module] ?? null) : null;
  const file = ledgerFile(module, mode);
  if (file === null) {
    return {
      ok: false,
      module,
      mode,
      schema,
      era: { key: era, from: start, note: suite.note, moduleEra },
      nRecords: 0,
      groups: [],
      exitReasons: { unavailable: `${module} has no live ledger` },
      heldBack: [],
      pairs: [],
      breaks: [],
      excursions: { ok: false, data: null, error: `${module} has no live ledger` },
      error: `${module} has no live ledger — its performance reading is paper only`,
    };
  }
  const dbPath = path.join(config.paths[MODULE_DIR_KEY[module]], file);

  // Independent of the metrics reading -- a query straight off the ledger, not through
  // metricsBridge -- so it's read whether or not the calibration reading itself succeeds; a
  // module whose ledger schema `core.metrics` doesn't yet know should still show its exit reasons.
  const exits = readExitReasons(config, module, file, moduleEra);
  const breaks = readBreaks(dbPath);
  // pmcc's own verb defaults to its current era, so ALL has to be asked for to widen it.
  const excursions = readExcursions(module, dbPath, module in MODULE_ERA ? (moduleEra ?? "ALL") : null);

  const res = readModuleMetrics(dbPath, schema, start, null, moduleEra);
  if (!res.ok || res.metrics === null) {
    return {
      ok: false,
      module,
      mode,
      schema,
      era: { key: era, from: start, note: suite.note, moduleEra },
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
  const plain: ModulePerformanceGroup[] = Object.entries(res.metrics.groups).map(([tag, g]: [string, ModuleMetricsGroup]) => ({
    tag,
    reading: g.reading,
    sessionNets: g.session_nets,
    tradeNets: g.trade_nets,
  }));
  // Flies has no per-trade capital, so `return_on_capital` is always empty for it; return on peak
  // risk stands in, per group, over the same window the reading covers.
  const groups =
    module === "flies"
      ? withReadOnlyDb(dbPath, plain, (db) =>
          plain.map((g) => ({ ...g, peakRisk: onPeakRiskOver(peakRiskSessions(db, { ...flyTagScope(g.tag), start })) })),
        )
      : plain;
  return {
    ok: true,
    module,
    mode,
    schema,
    era: { key: era, from: start, note: suite.note, moduleEra },
    nRecords: res.metrics.n_records,
    groups,
    exitReasons: exits.exitReasons,
    heldBack: exits.heldBack,
    // Advised books are paper-only by construction: a live book shadows no experiment.
    pairs: mode === "paper" ? readAdvisedPairs(config, module, groups) : [],
    breaks,
    excursions,
    error: null,
  };
}
