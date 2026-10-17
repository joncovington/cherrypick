import type { PerformanceModuleId, PortfolioReading, TradingMode } from "@console/shared";
import type { ConsoleConfig } from "../config.js";
import { suiteEra } from "./db.js";
import { MODULE_ERA, MODULE_SCHEMA, moduleLedgerPath } from "./performance.js";
import { spawnModuleCliAsync } from "../services/moduleCli.js";

/**
 * Compare's portfolio tab (`GET /api/portfolio`): the chosen arms, one per module, read by
 * `python -m cherrypick.core.portfolio` -- a bridge, not a mirror. Each arm resolves to its module's
 * ledger, schema and era exactly as the P&L calendar's do (`moduleLedgerPath`, `MODULE_SCHEMA`,
 * `MODULE_ERA`), over the suite window, so a session net there is the calendar's cell. The window
 * ends at the latest close any chosen arm made (core's default). Memoised two minutes per selection.
 */

const UNAVAILABLE = "portfolio unavailable — cherrypick-core must be installed (pip install -e packages/core)";
const TTL_MS = 120_000;
const cache = new Map<string, { at: number; value: PortfolioReading }>();

export interface PortfolioQuery {
  /** `<module>:<group tag>` per arm; the module is the part before the first colon. */
  arms: string[];
  period: "day" | "week";
  weights: "equal_risk" | "booked";
  mode: TradingMode;
}

function isModule(v: string): v is PerformanceModuleId {
  return Object.prototype.hasOwnProperty.call(MODULE_SCHEMA, v);
}

type Caller = (argv: string[]) => Promise<PortfolioReading>;

async function spawn(argv: string[]): Promise<PortfolioReading> {
  const res = await spawnModuleCliAsync(argv, UNAVAILABLE);
  if (!res.ok || res.json === null) return { ok: false, error: res.error } as PortfolioReading;
  if (res.json["ok"] !== true) {
    const err = res.json["error"];
    return { ok: false, error: typeof err === "string" ? err : `${UNAVAILABLE} — refused` } as PortfolioReading;
  }
  return res.json as unknown as PortfolioReading;
}

let caller: Caller = spawn;

/** Swap the subprocess out in tests. Pass nothing to restore the real one. */
export function setPortfolioCaller(fn?: Caller): void {
  caller = fn ?? spawn;
  cache.clear();
}

export async function readPortfolio(config: ConsoleConfig, q: PortfolioQuery, now = Date.now()): Promise<PortfolioReading> {
  const manifest = [];
  const unknown: string[] = [];
  for (const key of q.arms) {
    const cut = key.indexOf(":");
    const module = cut > 0 ? key.slice(0, cut) : "";
    const tag = cut > 0 ? key.slice(cut + 1) : "";
    const db = isModule(module) ? moduleLedgerPath(config, module, q.mode) : null;
    if (!isModule(module) || tag === "" || db === null) {
      unknown.push(key);
      continue;
    }
    manifest.push({ key, db, schema: MODULE_SCHEMA[module], tag, era: MODULE_ERA[module] ?? null });
  }
  const start = suiteEra(config.paths.orchestratorConfig).from ?? "2026-01-01";
  const argv = ["-m", "cherrypick.core.portfolio", "read", "--manifest", JSON.stringify(manifest), "--start", start];
  argv.push("--period", q.period, "--weights", q.weights);
  const cacheKey = argv.join("\u0000");
  const hit = cache.get(cacheKey);
  let value: PortfolioReading;
  if (hit !== undefined && now - hit.at < TTL_MS) {
    value = hit.value;
  } else {
    value = await caller(argv);
    cache.set(cacheKey, { at: now, value });
  }
  if (!value.ok) return { ...value, mode: q.mode };
  // An arm the console could not resolve (unknown module, no ledger in this mode) is named beside
  // core's own failures, never silently dropped from the portfolio.
  const failures = [...(value.failures ?? []), ...unknown.map((key) => ({ key, error: `no ${q.mode} ledger for ${key}` }))];
  return { ...value, mode: q.mode, failures };
}
