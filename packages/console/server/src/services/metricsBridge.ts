import type { PnlTrade } from "@console/shared";
import { spawnModuleCli, spawnModuleCliAsync, type SpawnedJson } from "./moduleCli.js";

/**
 * The shared calibration-reading bundle, read from the module that owns the math.
 *
 * `python -m cherrypick.core.metrics read` runs the exact normalise-then-summarize pipeline
 * `orchestrator.report`/`calibrate` already run over a module's ledger -- `ledgers.READERS` +
 * `profiles.group_by_tag` + `calibration_reading` -- for one schema, grouped by that schema's
 * own profile tag. A TypeScript re-derivation would be a second implementation of `calibration_
 * reading`'s ~20 metrics free to drift from the one `calibrate` actually promotes on, which is the
 * exact mistake `services/report.ts` already made once (its own docstring calls the net rules
 * "copied exactly" and they had already drifted from the orchestrator's). Same bridging pattern
 * and the same reason as `configBridge.ts`/`calendarsBridge.ts`/`screenBridge.ts`.
 *
 * Field names inside `reading` stay snake_case, matching `calibration_reading`'s own JSON verbatim
 * (the `ScreenMetrics`/`funnel` convention `screenBridge.ts` already uses) -- a ~20-key hand
 * mapping to camelCase would be a second place for those names to drift apart.
 */

export interface ModuleMetricsGroup {
  reading: Record<string, unknown>;
  /** [session, net] pairs in session order -- `core.metrics.session_nets_dated`. */
  session_nets: Array<[string, number]>;
  /** Raw per-trade net P&L, in the same row order the schema's reader returned them. */
  trade_nets: number[];
}

export interface ModuleMetrics {
  schema: string;
  n_records: number;
  groups: Record<string, ModuleMetricsGroup>;
}

export interface ModuleMetricsResult {
  ok: boolean;
  metrics: ModuleMetrics | null;
  error: string | null;
}

const UNAVAILABLE =
  "calibration metrics unavailable — cherrypick-core must be installed (pip install -e packages/core)";

function argvFor(dbPath: string, schema: string, start: string | null, end: string | null, era: string | null): string[] {
  const argv = ["-m", "cherrypick.core.metrics", "read", "--db", dbPath, "--schema", schema];
  if (start !== null) argv.push("--start", start);
  if (end !== null) argv.push("--end", end);
  // The module's own stamped era (`ledgers.ERA_SCHEMAS` only -- the CLI refuses any other schema).
  if (era !== null) argv.push("--era", era);
  return argv;
}

function spawnCaller(
  dbPath: string,
  schema: string,
  start: string | null,
  end: string | null,
  era: string | null = null,
): ModuleMetricsResult {
  return shape(spawnModuleCli(argvFor(dbPath, schema, start, end, era), UNAVAILABLE), schema);
}

/** The same read without blocking the event loop (`spawnModuleCliAsync`), shaped by the same code. */
async function spawnCallerAsync(
  dbPath: string,
  schema: string,
  start: string | null,
  end: string | null,
  era: string | null = null,
): Promise<ModuleMetricsResult> {
  return shape(await spawnModuleCliAsync(argvFor(dbPath, schema, start, end, era), UNAVAILABLE), schema);
}

function shape(res: SpawnedJson, schema: string): ModuleMetricsResult {
  if (!res.ok || res.json === null) return { ok: false, metrics: null, error: res.error };
  // The CLI itself reports {"ok": false, "error": ...} for an unknown schema or an unreadable db
  // (never a traceback across the subprocess boundary) -- surface that the same way a spawn
  // failure is surfaced, so the card head shows one error shape regardless of which layer refused.
  if (res.json["ok"] !== true) {
    const err = res.json["error"];
    return { ok: false, metrics: null, error: typeof err === "string" ? err : `${UNAVAILABLE} — refused` };
  }
  return {
    ok: true,
    metrics: {
      schema: typeof res.json["schema"] === "string" ? (res.json["schema"] as string) : schema,
      n_records: typeof res.json["n_records"] === "number" ? (res.json["n_records"] as number) : 0,
      groups: (res.json["groups"] ?? {}) as Record<string, ModuleMetricsGroup>,
    },
    error: null,
  };
}

let caller = spawnCaller;
let asyncCaller = spawnCallerAsync;

/** Swap the subprocess out in tests (both paths). Pass nothing to restore the real ones. */
export function setMetricsCaller(fn?: typeof spawnCaller): void {
  caller = fn ?? spawnCaller;
  asyncCaller = fn === undefined ? spawnCallerAsync : (...args) => Promise.resolve(fn(...args));
}

// A calibration reading replays a whole ledger through ~20 metric functions, and the answer moves
// only when a new trade closes. Every performance slide polls; this must not turn a poll into a
// subprocess.
const TTL_MS = 120_000;
const cache = new Map<string, { at: number; value: ModuleMetricsResult }>();

export function readModuleMetrics(
  dbPath: string,
  schema: string,
  start: string | null,
  end: string | null,
  era: string | null = null,
  now = Date.now(),
): ModuleMetricsResult {
  const key = `${dbPath}|${schema}|${start ?? ""}|${end ?? ""}|${era ?? ""}`;
  const hit = cache.get(key);
  if (hit !== undefined && now - hit.at < TTL_MS) return hit.value;
  const value = caller(dbPath, schema, start, end, era);
  cache.set(key, { at: now, value });
  return value;
}

// Reads in flight, by the same key: a second request for a ledger being read waits on the first.
const inflight = new Map<string, Promise<ModuleMetricsResult>>();

/** `readModuleMetrics` without blocking the server: the same cache, one subprocess per key at a time
 *  (the P&L calendar reads every module's ledger at once through this). */
export function readModuleMetricsAsync(
  dbPath: string,
  schema: string,
  start: string | null,
  end: string | null,
  era: string | null = null,
  now = Date.now(),
): Promise<ModuleMetricsResult> {
  const key = `${dbPath}|${schema}|${start ?? ""}|${end ?? ""}|${era ?? ""}`;
  const hit = cache.get(key);
  if (hit !== undefined && now - hit.at < TTL_MS) return Promise.resolve(hit.value);
  const pending = inflight.get(key);
  if (pending !== undefined) return pending;
  const read = asyncCaller(dbPath, schema, start, end, era).then((value) => {
    cache.set(key, { at: Date.now(), value });
    return value;
  });
  inflight.set(key, read);
  void read.finally(() => inflight.delete(key));
  return read;
}

// ------------------------------------------------------------------ one arm's trades on one session
/**
 * `python -m cherrypick.core.metrics trades`: the records behind one group's `session_nets` entry,
 * read by the same `_records` in core, so the rows always sum to the calendar cell they drill from.
 * Not memoised: it runs on a click, over one session.
 */
export interface ModuleTradesResult {
  ok: boolean;
  trades: PnlTrade[];
  error: string | null;
}

function spawnTrades(dbPath: string, schema: string, session: string, tag: string, era: string | null) {
  const argv = ["-m", "cherrypick.core.metrics", "trades", "--db", dbPath, "--schema", schema];
  argv.push("--start", session, "--end", session, "--tag", tag);
  if (era !== null) argv.push("--era", era);
  return spawnModuleCliAsync(argv, UNAVAILABLE).then((res): ModuleTradesResult => {
    if (!res.ok || res.json === null) return { ok: false, trades: [], error: res.error };
    if (res.json["ok"] !== true) {
      const err = res.json["error"];
      return { ok: false, trades: [], error: typeof err === "string" ? err : `${UNAVAILABLE} — refused` };
    }
    return { ok: true, trades: (res.json["trades"] ?? []) as PnlTrade[], error: null };
  });
}

let tradesCaller = spawnTrades;

/** Swap the subprocess out in tests. Pass nothing to restore the real one. */
export function setTradesCaller(fn?: typeof spawnTrades): void {
  tradesCaller = fn ?? spawnTrades;
}

export function readModuleTradesAsync(
  dbPath: string,
  schema: string,
  session: string,
  tag: string,
  era: string | null = null,
): Promise<ModuleTradesResult> {
  return tradesCaller(dbPath, schema, session, tag, era);
}

/** Drop the memoised readings. Tests only. */
export function resetMetricsCache(): void {
  cache.clear();
  inflight.clear();
}
