import type {
  ArmAnalysis,
  ArmRiskRow,
  ArmsRiskResult,
  ModuleAnalysisResult,
  PerformanceModuleId,
  TradingMode,
} from "@console/shared";
import type { ConsoleConfig } from "../config.js";
import { suiteEra } from "./db.js";
import { LIVE_LEDGER, MODULE_ERA, MODULE_SCHEMA, moduleLedgerPath } from "./performance.js";
import { readAnalysisAsync } from "../services/analysisBridge.js";

/**
 * One module's strategy analysis, per arm, via `services/analysisBridge.ts`
 * (docs/strategy-analysis-plan.md). It reads exactly what the performance slide reads -- the same
 * ledger file for the mode, the same suite epoch, the same stamped module era -- so a figure here
 * and the reading beside it always describe the same trades. The module's id goes to the bridge
 * so its declared allocations apply; the file is read by core, never here. Nothing is computed in
 * this file. Async (`readAnalysisAsync`): a subprocess never holds the server's event loop.
 */
export async function readModuleAnalysis(
  config: ConsoleConfig,
  module: PerformanceModuleId,
  era: "current" | "ALL" = "current",
  mode: TradingMode = "paper",
): Promise<ModuleAnalysisResult> {
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
  const res = await readAnalysisAsync(dbPath, schema, start, null, moduleEra, module);
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

/** One arm's row for the Compare page's risk tab: fields picked from its reading, never computed. */
export function riskRow(module: PerformanceModuleId, tag: string, a: ArmAnalysis): ArmRiskRow {
  const reading = a.reading;
  const net = typeof reading["net_pnl"] === "number" ? (reading["net_pnl"] as number) : null;
  const top10 = a.durability.without_top.find((r) => Math.abs(r.fraction - 0.1) < 1e-9) ?? null;
  return {
    module,
    tag,
    window: a.window ?? null,
    trades: a.coverage.trades,
    sessions: a.stress.sessions,
    net,
    peakRisk: a.capital?.peak_risk.peak ?? null,
    netOverLargestPeak: a.capital?.return_on_peak_risk ?? null,
    maxDrawdown: a.stress.historical,
    drawdownOverPeak: a.capital?.max_drawdown_over_peak_risk ?? null,
    stressBlocks: a.stress.grouped,
    stressWithheld: a.stress.withheld,
    withoutTop10: top10?.net ?? null,
    top10SignFlips: top10?.sign_flips ?? false,
    currentRun: a.session_streaks.current,
    breakevenMultiple: a.execution?.breakeven_multiple ?? null,
  };
}

/**
 * Every arm of every module, for the Compare page's risk tab (`GET /api/analysis`). Each module is
 * read through `readModuleAnalysis` (memoised per module), so this costs nothing a module's own
 * analysis tab has not already paid. A module whose read fails is named in `failures`, never shown
 * as an empty section; in live mode a module with no live ledger is simply not part of the answer.
 */
export async function readArmsRisk(config: ConsoleConfig, mode: TradingMode = "paper"): Promise<ArmsRiskResult> {
  const modules = (Object.keys(MODULE_SCHEMA) as PerformanceModuleId[]).filter(
    (module) => mode === "paper" || module in LIVE_LEDGER,
  );
  // Every module at once: eight subprocesses side by side, so a cold read costs the slowest module
  // (MEIC, ~3 s), not their sum. Results are folded back in the registry's order.
  const results = await Promise.all(modules.map((module) => readModuleAnalysis(config, module, "current", mode)));
  const rows: ArmRiskRow[] = [];
  const failures: ArmsRiskResult["failures"] = [];
  results.forEach((res, i) => {
    const module = modules[i] as PerformanceModuleId;
    if (!res.ok) {
      failures.push({ module, error: res.error ?? "analysis unavailable" });
      return;
    }
    for (const g of res.groups) rows.push(riskRow(module, g.tag, g.analysis));
  });
  return { mode, rows, failures };
}
