import { BACKTEST_ARM_PREFIX, type PerformanceModuleId, type PortfolioReading, type PortfolioSource, type PortfolioSources, type TradingMode } from "@console/shared";
import type { ConsoleConfig } from "../config.js";
import { suiteEra } from "./db.js";
import { MODULE_ERA, MODULE_SCHEMA, moduleLedgerPath } from "./performance.js";
import { spawnModuleCliAsync } from "../services/moduleCli.js";
import { backtestResultsOn, getFeatures } from "../services/featuresBridge.js";

/**
 * Compare's portfolio tab (`GET /api/portfolio`): the chosen arms, one per module, read by
 * `python -m cherrypick.core.portfolio` -- a bridge, not a mirror. Each paper or live arm resolves to
 * its module's ledger, schema and era exactly as the P&L calendar's do (`moduleLedgerPath`,
 * `MODULE_SCHEMA`, `MODULE_ERA`), over the suite window, so a session net there is the calendar's cell.
 *
 * A backtest arm (`bt:<source id>#<arm>`) is one arm of a Builder run or study result, listed by
 * `python -m cherrypick.backtest read portfolio-sources` (`GET /api/portfolio/backtests`). Its key names
 * a listed source, never a path: the server resolves the id against the listing, so nothing outside it
 * is read. A licensed source is listed and read only while the orchestrator's results gate is open
 * (`backtestResultsOn`), the same gate every other licensed result answers to. With a backtest chosen,
 * the window starts at the earliest chosen run's first session, not the suite's.
 *
 * The window ends at the latest close any chosen arm made (core's default). Memoised two minutes per
 * selection; the sources listing ten minutes.
 */

const UNAVAILABLE = "portfolio unavailable — cherrypick-core must be installed (pip install -e packages/core)";
const SOURCES_UNAVAILABLE = "backtest results unavailable — cherrypick-backtest must be installed";
const TTL_MS = 120_000;
const SOURCES_TTL_MS = 600_000;
const cache = new Map<string, { at: number; value: PortfolioReading }>();

export interface PortfolioQuery {
  /** `<module>:<group tag>` per paper/live arm (the module is the part before the first colon), or `bt:<source id>#<arm>`. */
  arms: string[];
  period: "day" | "week";
  weights: "equal_risk" | "booked";
  mode: TradingMode;
}

/** A listed source with the path core reads; the path never leaves the server. */
type ListedSource = PortfolioSource & { path: string };

function isModule(v: string): v is PerformanceModuleId {
  return Object.prototype.hasOwnProperty.call(MODULE_SCHEMA, v);
}

type Caller = (argv: string[]) => Promise<PortfolioReading>;
type SourcesCaller = () => Promise<{ ok: boolean; error?: string; sources?: ListedSource[]; unreadable?: string[] }>;
type GateCaller = () => Promise<boolean>;

async function spawn(argv: string[]): Promise<PortfolioReading> {
  const res = await spawnModuleCliAsync(argv, UNAVAILABLE);
  if (!res.ok || res.json === null) return { ok: false, error: res.error } as PortfolioReading;
  if (res.json["ok"] !== true) {
    const err = res.json["error"];
    return { ok: false, error: typeof err === "string" ? err : `${UNAVAILABLE} — refused` } as PortfolioReading;
  }
  return res.json as unknown as PortfolioReading;
}

async function spawnSources(): ReturnType<SourcesCaller> {
  const res = await spawnModuleCliAsync(["-m", "cherrypick.backtest", "read", "portfolio-sources"], SOURCES_UNAVAILABLE);
  if (!res.ok || res.json === null) return { ok: false, error: res.error ?? SOURCES_UNAVAILABLE };
  return res.json as unknown as Awaited<ReturnType<SourcesCaller>>;
}

let caller: Caller = spawn;
let sourcesCaller: SourcesCaller = spawnSources;
let gateCaller: GateCaller = async () => backtestResultsOn(await getFeatures());
let sourcesMemo: { at: number; value: Awaited<ReturnType<SourcesCaller>> } | null = null;

/** Swap the subprocesses (and the results gate) out in tests. Pass nothing to restore the real ones. */
export function setPortfolioCaller(fn?: Caller, sources?: SourcesCaller, gate?: GateCaller): void {
  caller = fn ?? spawn;
  sourcesCaller = sources ?? spawnSources;
  gateCaller = gate ?? (async () => backtestResultsOn(await getFeatures()));
  cache.clear();
  sourcesMemo = null;
}

async function listed(now: number): Promise<{ ok: boolean; error?: string; sources: ListedSource[]; unreadable: string[] }> {
  if (sourcesMemo === null || now - sourcesMemo.at >= SOURCES_TTL_MS) {
    sourcesMemo = { at: now, value: await sourcesCaller() };
  }
  const v = sourcesMemo.value;
  if (!v.ok) return { ok: false, error: v.error ?? SOURCES_UNAVAILABLE, sources: [], unreadable: [] };
  // A closed results gate withholds every licensed source: not listed, not read.
  const open = await gateCaller();
  const sources = (v.sources ?? []).filter((s) => open || !s.licensed);
  return { ok: true, sources, unreadable: v.unreadable ?? [] };
}

/** The backtest results the tab can hold beside paper arms, paths withheld. */
export async function readPortfolioSources(now = Date.now()): Promise<PortfolioSources> {
  const l = await listed(now);
  if (!l.ok) return { ok: false, error: l.error ?? null, sources: [], unreadable: [] };
  return { ok: true, sources: l.sources.map(({ path: _path, ...s }) => s), unreadable: l.unreadable };
}

export async function readPortfolio(config: ConsoleConfig, q: PortfolioQuery, now = Date.now()): Promise<PortfolioReading> {
  const manifest: Array<Record<string, unknown>> = [];
  const unknown: Array<{ key: string; error: string }> = [];
  let start = suiteEra(config.paths.orchestratorConfig).from ?? "2026-01-01";
  const wantsBacktest = q.arms.some((k) => k.startsWith(BACKTEST_ARM_PREFIX));
  const sources = wantsBacktest ? await listed(now) : null;
  for (const key of q.arms) {
    if (key.startsWith(BACKTEST_ARM_PREFIX)) {
      const cut = key.lastIndexOf("#");
      const id = cut > 0 ? key.slice(BACKTEST_ARM_PREFIX.length, cut) : "";
      const arm = cut > 0 ? key.slice(cut + 1) : "";
      const src = sources?.sources.find((s) => s.id === id);
      if (src === undefined || !src.arms.includes(arm)) {
        unknown.push({ key, error: sources?.ok === false ? (sources.error ?? SOURCES_UNAVAILABLE) : `no backtest arm ${key}` });
        continue;
      }
      manifest.push({ key, kind: "backtest", path: src.path, arm, licensed: src.licensed });
      if (src.window !== null && src.window[0] < start) start = src.window[0];
      continue;
    }
    const cut = key.indexOf(":");
    const module = cut > 0 ? key.slice(0, cut) : "";
    const tag = cut > 0 ? key.slice(cut + 1) : "";
    const db = isModule(module) ? moduleLedgerPath(config, module, q.mode) : null;
    if (!isModule(module) || tag === "" || db === null) {
      unknown.push({ key, error: `no ${q.mode} ledger for ${key}` });
      continue;
    }
    manifest.push({ key, db, schema: MODULE_SCHEMA[module], tag, era: MODULE_ERA[module] ?? null });
  }
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
  // An arm the console could not resolve (unknown module, no ledger in this mode, a backtest not
  // listed) is named beside core's own failures, never silently dropped from the portfolio.
  const failures = [...(value.failures ?? []), ...unknown];
  return { ...value, mode: q.mode, failures };
}
