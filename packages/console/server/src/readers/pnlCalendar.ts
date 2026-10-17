import type {
  PerformanceModuleId,
  PnlCalendarResult,
  PnlDay,
  PnlExcursion,
  PnlTrace,
  PnlTradesResult,
  TradingMode,
} from "@console/shared";
import { readExcursionAsync } from "../services/excursionBridge.js";
import { readTraceAsync } from "../services/traceBridge.js";
import type { ConsoleConfig } from "../config.js";
import { suiteEra } from "./db.js";
import { LIVE_LEDGER, MODULE_ERA, MODULE_SCHEMA, moduleLedgerPath } from "./performance.js";
import { readModuleMetricsAsync, readModuleTradesAsync } from "../services/metricsBridge.js";

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

/**
 * One arm's trades on one session, for the calendar's day drill (`GET /api/calendar/trades`): the
 * same ledger, schema and era the calendar read, so the rows sum to the cell clicked.
 */
export async function readPnlTrades(
  config: ConsoleConfig,
  module: PerformanceModuleId,
  session: string,
  tag: string,
  mode: TradingMode = "paper",
): Promise<PnlTradesResult> {
  const base = { module, session, tag, mode };
  const dbPath = moduleLedgerPath(config, module, mode);
  if (dbPath === null) return { ...base, ok: false, trades: [], error: `${module} keeps no ${mode} ledger` };
  const res = await readModuleTradesAsync(dbPath, MODULE_SCHEMA[module], session, tag, MODULE_ERA[module] ?? null);
  return { ...base, ...res };
}

/** One position's excursion, from the ledger the calendar's trades came from
 *  (`GET /api/calendar/excursion`). */
export async function readPnlExcursion(
  config: ConsoleConfig,
  module: PerformanceModuleId,
  positionId: string,
  mode: TradingMode = "paper",
): Promise<PnlExcursion> {
  const dbPath = moduleLedgerPath(config, module, mode);
  if (dbPath === null) return { ok: false, error: `${module} keeps no ${mode} ledger` };
  return readExcursionAsync(dbPath, MODULE_SCHEMA[module], positionId);
}

/** One position's entry and management trace, from the same ledger (`GET /api/calendar/trace`). */
export async function readPnlTrace(
  config: ConsoleConfig,
  module: PerformanceModuleId,
  positionId: string,
  mode: TradingMode = "paper",
): Promise<PnlTrace> {
  const dbPath = moduleLedgerPath(config, module, mode);
  if (dbPath === null) return { ok: false, error: `${module} keeps no ${mode} ledger` };
  return readTraceAsync(dbPath, MODULE_SCHEMA[module], positionId);
}
