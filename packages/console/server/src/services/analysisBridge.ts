import { ANALYSIS_VERSION, type ArmAnalysis } from "@console/shared";
import { spawnModuleCli } from "./moduleCli.js";

/**
 * The per-arm strategy analysis, read from the module that owns the math.
 *
 * `python -m cherrypick.core.analysis read` runs `core.ledgers`' reader for one schema and
 * `core.analysis.arm_reading` per arm: streaks, drawdown episodes, profit durability and the
 * reshuffle stress test (docs/strategy-analysis-plan.md). A derivation, so it is bridged, never
 * mirrored -- the same rule and pattern as `metricsBridge.ts`, whose grouping it shares, so the
 * performance and analysis slides always describe the same arms.
 *
 * A reply whose `version` is not `ANALYSIS_VERSION` is refused: a field that changed meaning would
 * otherwise render under the old label.
 */

export interface ModuleAnalysis {
  schema: string;
  n_records: number;
  groups: Record<string, ArmAnalysis>;
}

export interface ModuleAnalysisBridgeResult {
  ok: boolean;
  analysis: ModuleAnalysis | null;
  error: string | null;
}

const UNAVAILABLE =
  "strategy analysis unavailable — cherrypick-core must be installed (pip install -e packages/core)";

function spawnCaller(
  dbPath: string,
  schema: string,
  start: string | null,
  end: string | null,
  era: string | null = null,
): ModuleAnalysisBridgeResult {
  const argv = ["-m", "cherrypick.core.analysis", "read", "--db", dbPath, "--schema", schema];
  if (start !== null) argv.push("--start", start);
  if (end !== null) argv.push("--end", end);
  if (era !== null) argv.push("--era", era);
  const res = spawnModuleCli(argv, UNAVAILABLE);
  if (!res.ok || res.json === null) return { ok: false, analysis: null, error: res.error };
  if (res.json["ok"] !== true) {
    const err = res.json["error"];
    return { ok: false, analysis: null, error: typeof err === "string" ? err : `${UNAVAILABLE} — refused` };
  }
  const version = res.json["version"];
  if (version !== ANALYSIS_VERSION) {
    return {
      ok: false,
      analysis: null,
      error: `strategy analysis version ${String(version)} — this console reads version ${ANALYSIS_VERSION}`,
    };
  }
  return {
    ok: true,
    analysis: {
      schema: typeof res.json["schema"] === "string" ? (res.json["schema"] as string) : schema,
      n_records: typeof res.json["n_records"] === "number" ? (res.json["n_records"] as number) : 0,
      groups: (res.json["groups"] ?? {}) as Record<string, ArmAnalysis>,
    },
    error: null,
  };
}

let caller = spawnCaller;

/** Swap the subprocess out in tests. Pass nothing to restore the real one. */
export function setAnalysisCaller(fn?: typeof spawnCaller): void {
  caller = fn ?? spawnCaller;
}

// The stress test reshuffles every arm's sessions 2,000 times; the answer moves only when a trade
// closes. Memoised like the metrics reading, so a poll is never a subprocess.
const TTL_MS = 120_000;
const cache = new Map<string, { at: number; value: ModuleAnalysisBridgeResult }>();

export function readAnalysis(
  dbPath: string,
  schema: string,
  start: string | null,
  end: string | null,
  era: string | null = null,
  now = Date.now(),
): ModuleAnalysisBridgeResult {
  const key = `${dbPath}|${schema}|${start ?? ""}|${end ?? ""}|${era ?? ""}`;
  const hit = cache.get(key);
  if (hit !== undefined && now - hit.at < TTL_MS) return hit.value;
  const value = caller(dbPath, schema, start, end, era);
  cache.set(key, { at: now, value });
  return value;
}

/** Drop the memoised analyses. Tests only. */
export function resetAnalysisCache(): void {
  cache.clear();
}
