import type { PerformanceModuleId, PnlCalendarResult, PnlDay, TradingMode } from "@console/shared";
import type { ConsoleConfig } from "../config.js";
import { suiteEra } from "./db.js";
import { LIVE_LEDGER, MODULE_ERA, MODULE_SCHEMA, moduleLedgerPath } from "./performance.js";
import { readModuleMetricsAsync } from "../services/metricsBridge.js";

/**
 * Every arm's net per session, for Compare's calendar tab (`GET /api/calendar`).
 *
 * The nets are `core.metrics`' own `session_nets_dated`, the series each module's performance tab
 * already reads, over the same window that tab reads: the suite epoch, and pmcc's stamped era. A
 * calendar answers "what happened that day", so it is NOT scoped to each arm's era the way the
 * analysis is; that would blank flies control's whole September. Every module is read at once
 * (`readModuleMetricsAsync`), memoised with the performance tab's reads. Nothing is summed here:
 * the component adds days and weeks up for display. A module whose read fails is named.
 */
export async function readPnlCalendar(config: ConsoleConfig, mode: TradingMode = "paper"): Promise<PnlCalendarResult> {
  const suite = suiteEra(config.paths.orchestratorConfig);
  const modules = (Object.keys(MODULE_SCHEMA) as PerformanceModuleId[]).filter(
    (module) => mode === "paper" || module in LIVE_LEDGER,
  );
  const results = await Promise.all(
    modules.map((module) => {
      const dbPath = moduleLedgerPath(config, module, mode);
      return dbPath === null
        ? Promise.resolve(null)
        : readModuleMetricsAsync(dbPath, MODULE_SCHEMA[module], suite.from, null, MODULE_ERA[module] ?? null);
    }),
  );
  const days: PnlDay[] = [];
  const failures: PnlCalendarResult["failures"] = [];
  results.forEach((res, i) => {
    const module = modules[i] as PerformanceModuleId;
    if (res === null) return;
    if (!res.ok || res.metrics === null) {
      failures.push({ module, error: res.error ?? "calibration reading unavailable" });
      return;
    }
    for (const [tag, group] of Object.entries(res.metrics.groups)) {
      for (const [session, net] of group.session_nets) days.push({ module, tag, session, net });
    }
  });
  return { mode, from: suite.from, note: suite.note, days, failures };
}
