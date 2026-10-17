import type { ModuleAnalysisResult, PerformanceModuleId, TradingMode } from "@console/shared";
import type { ConsoleConfig } from "../config.js";
import { suiteEra } from "./db.js";
import { MODULE_ERA, MODULE_SCHEMA, moduleLedgerPath } from "./performance.js";
import { readAnalysis } from "../services/analysisBridge.js";

/**
 * One module's strategy analysis, per arm, via `services/analysisBridge.ts`
 * (docs/strategy-analysis-plan.md). It reads exactly what the performance slide reads -- the same
 * ledger file for the mode, the same suite epoch, the same stamped module era -- so a figure here
 * and the reading beside it always describe the same trades. The module's id goes to the bridge
 * so its declared allocations apply; the file is read by core, never here. Nothing is computed in
 * this file.
 */
export function readModuleAnalysis(
  config: ConsoleConfig,
  module: PerformanceModuleId,
  era: "current" | "ALL" = "current",
  mode: TradingMode = "paper",
): ModuleAnalysisResult {
  const schema = MODULE_SCHEMA[module];
  const suite = suiteEra(config.paths.orchestratorConfig);
  const start = era === "current" ? suite.from : null;
  const moduleEra = era === "current" ? (MODULE_ERA[module] ?? null) : null;
  const eraOut = { key: era, from: start, note: suite.note, moduleEra };
  const none = { path: null, error: null };
  const dbPath = moduleLedgerPath(config, module, mode);
  if (dbPath === null) {
    const error = `${module} has no live ledger — its analysis is paper only`;
    return { ok: false, module, mode, schema, era: eraOut, nRecords: 0, allocations: none, groups: [], error };
  }
  const res = readAnalysis(dbPath, schema, start, null, moduleEra, module);
  if (!res.ok || res.analysis === null) {
    return { ok: false, module, mode, schema, era: eraOut, nRecords: 0, allocations: none, groups: [], error: res.error };
  }
  const groups = Object.entries(res.analysis.groups).map(([tag, analysis]) => ({ tag, analysis }));
  return {
    ok: true,
    module,
    mode,
    schema,
    era: eraOut,
    nRecords: res.analysis.n_records,
    allocations: res.analysis.allocations,
    groups,
    error: null,
  };
}
