import { spawnSync } from "node:child_process";

/**
 * The shared calibration-reading bundle, read from the module that owns the math.
 *
 * `python -m cherrypick.core.metrics read` runs the exact normalise-then-summarize pipeline
 * `orchestrator.report`/`calibrate` already run over a module's ledger -- `ledgers.READERS` +
 * `profiles.compare_profiles` + `calibration_reading` -- for one schema, grouped by that schema's
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

function spawnCaller(dbPath: string, schema: string, start: string | null, end: string | null): ModuleMetricsResult {
  const argv = ["-m", "cherrypick.core.metrics", "read", "--db", dbPath, "--schema", schema];
  if (start !== null) argv.push("--start", start);
  if (end !== null) argv.push("--end", end);
  let out;
  try {
    out = spawnSync("python", argv, { encoding: "utf-8", timeout: 30_000, windowsHide: true });
  } catch (err) {
    return { ok: false, metrics: null, error: `${UNAVAILABLE} (${(err as Error).message})` };
  }
  if (out.error !== undefined) return { ok: false, metrics: null, error: `${UNAVAILABLE} (${out.error.message})` };
  if (out.status !== 0) {
    const detail = (out.stderr ?? "").trim().split(/\r?\n/).pop() ?? `exit ${String(out.status)}`;
    return { ok: false, metrics: null, error: `${UNAVAILABLE} — ${detail}` };
  }
  let parsed: { ok: boolean; error?: string; schema?: string; n_records?: number; groups?: unknown };
  try {
    parsed = JSON.parse(out.stdout.trim()) as typeof parsed;
  } catch {
    return { ok: false, metrics: null, error: `${UNAVAILABLE} — unparseable response` };
  }
  // The CLI itself reports {"ok": false, "error": ...} for an unknown schema or an unreadable db
  // (never a traceback across the subprocess boundary) -- surface that the same way a spawn
  // failure is surfaced, so the card head shows one error shape regardless of which layer refused.
  if (!parsed.ok) {
    return { ok: false, metrics: null, error: parsed.error ?? `${UNAVAILABLE} — refused` };
  }
  return {
    ok: true,
    metrics: {
      schema: parsed.schema ?? schema,
      n_records: parsed.n_records ?? 0,
      groups: (parsed.groups ?? {}) as Record<string, ModuleMetricsGroup>,
    },
    error: null,
  };
}

let caller = spawnCaller;

/** Swap the subprocess out in tests. Pass nothing to restore the real one. */
export function setMetricsCaller(fn?: typeof spawnCaller): void {
  caller = fn ?? spawnCaller;
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
  now = Date.now(),
): ModuleMetricsResult {
  const key = `${dbPath}|${schema}|${start ?? ""}|${end ?? ""}`;
  const hit = cache.get(key);
  if (hit !== undefined && now - hit.at < TTL_MS) return hit.value;
  const value = caller(dbPath, schema, start, end);
  cache.set(key, { at: now, value });
  return value;
}

/** Drop the memoised readings. Tests only. */
export function resetMetricsCache(): void {
  cache.clear();
}
