import { spawnSync } from "node:child_process";

/**
 * The earnings screening metrics, read from the module that owns them.
 *
 * The console used to build its own rejection histogram straight off `scan_log`, and it disagreed
 * with `screen_report` about which gate to move — the question the table exists to answer. Two
 * reasons, both structural rather than sloppy: `scan_log` has accumulated four incompatible
 * vocabularies (the current binary accept/reject, the retired graded tier ladder, position closes
 * the exit path logs to the same table, and strategies removed from the suite) and pooling them
 * inflates whatever the old regimes emitted most; and a raw histogram has no sole-blocker column, so
 * it ranks gates that fire constantly but never alone, which a threshold change cannot rescue.
 *
 * `screen_metrics.classify` already solves both, so this asks it rather than re-deriving it. Same
 * bridging pattern and the same reason as `configBridge.ts`: the authority stays in one place.
 */

export interface ScreenReason {
  reason: string;
  total: number;
  /** Rejections where this gate was the ONLY blocker — the only ones a threshold change rescues. */
  sole: number;
  strategies: number;
}

export interface ScreenMetrics {
  profile: string;
  since: string | null;
  funnel: {
    prefiltered_symbols: number;
    screened_decisions: number;
    screened_symbols: number;
    accepted: number;
    rejected: number;
    execution_recorded: number;
    opened: number;
    dropped: number;
  };
  reasons: ScreenReason[];
  /**
   * The individual rejections blocked by exactly one gate — rows, not per-reason totals. Named the
   * same as `ScreenReason.sole` and easily confused with it: that one counts, this one lists.
   */
  sole: Array<{
    scan_date: string;
    symbol: string;
    strategy: string;
    reason: string;
    measured: number | null;
    threshold: number | null;
    comparator: string | null;
  }>;
  /** Rows the classifier set aside, and why — printed rather than silently dropped. */
  excluded: Array<{ label: string; rows: number }>;
  coverage: Record<string, unknown>;
}

export interface ScreenMetricsResult {
  ok: boolean;
  metrics: ScreenMetrics | null;
  error: string | null;
}

const UNAVAILABLE =
  "screening metrics unavailable — the earnings package must be installed (pip install -e packages/earnings)";

function spawnCaller(mode: "paper" | "live", since: string | null): ScreenMetricsResult {
  const argv = ["-m", "cherrypick.earnings.screen_report", "--json", "--mode", mode, "--limit", "25"];
  if (since !== null) argv.push("--since", since);
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
  try {
    return { ok: true, metrics: JSON.parse(out.stdout.trim()) as ScreenMetrics, error: null };
  } catch {
    return { ok: false, metrics: null, error: `${UNAVAILABLE} — unparseable response` };
  }
}

let caller = spawnCaller;

/** Swap the subprocess out in tests. Pass nothing to restore the real one. */
export function setScreenCaller(fn?: typeof spawnCaller): void {
  caller = fn ?? spawnCaller;
}

// Classifying the whole scan history costs a subprocess and a full table read, and the answer moves
// only when a scan runs. The Earnings page polls; this must not.
const TTL_MS = 120_000;
const cache = new Map<string, { at: number; value: ScreenMetricsResult }>();

export function readScreenMetrics(
  mode: "paper" | "live",
  since: string | null,
  now = Date.now(),
): ScreenMetricsResult {
  const key = `${mode}|${since ?? ""}`;
  const hit = cache.get(key);
  if (hit !== undefined && now - hit.at < TTL_MS) return hit.value;
  const value = caller(mode, since);
  cache.set(key, { at: now, value });
  return value;
}

/** Drop the memoised metrics. Tests only. */
export function resetScreenCache(): void {
  cache.clear();
}
