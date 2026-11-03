import type { Verdict, VerdictResult } from "@console/shared";
import { AsyncMemo } from "./asyncMemo.js";
import { spawnModuleCliAsync } from "./moduleCli.js";

/**
 * The earnings strategy verdict, read from the module that owns it (`strategy_report --json`,
 * `cherrypick.earnings.verdict`). Same bridging pattern and the same reason as `screenBridge.ts`:
 * the interval, the event clustering and the flagged-row rules live in one place, and the
 * strategies page draws them rather than computing a second set that could disagree.
 *
 * Paper only: the strat_test arms and the advised twins are paper books by construction.
 */

const UNAVAILABLE =
  "strategy verdict unavailable — the earnings package must be installed (pip install -e packages/earnings)";

async function spawnCaller(since: string | null, includeFlagged: boolean): Promise<VerdictResult> {
  const argv = ["-m", "cherrypick.earnings.strategy_report", "--json", "--mode", "paper"];
  if (since !== null) argv.push("--since", since);
  if (includeFlagged) argv.push("--include-flagged");
  const res = await spawnModuleCliAsync(argv, UNAVAILABLE);
  if (!res.ok || res.json === null) return { ok: false, verdict: null, error: res.error };
  return { ok: true, verdict: res.json as unknown as Verdict, error: null };
}

type Caller = (since: string | null, includeFlagged: boolean) => VerdictResult | Promise<VerdictResult>;
let caller: Caller = spawnCaller;

/** Swap the subprocess out in tests. Pass nothing to restore the real one. */
export function setVerdictCaller(fn?: Caller): void {
  caller = fn ?? spawnCaller;
}

// A subprocess and a full ledger read with a 2,000-draw bootstrap per arm; the answer moves only
// when a position closes. The page polls; this must not.
const TTL_MS = 120_000;
const cache = new AsyncMemo<VerdictResult>();

export function readVerdict(since: string | null, includeFlagged: boolean, now = Date.now()): Promise<VerdictResult> {
  return cache.get(`${since ?? ""}|${includeFlagged ? 1 : 0}`, TTL_MS, now, () => caller(since, includeFlagged));
}

/** order_id → flag rule, over the whole ledger (the verdict lists every flagged row, whatever its scope). */
export async function readFlaggedOrders(now = Date.now()): Promise<Map<string, string>> {
  const res = await readVerdict(null, false, now);
  return new Map((res.verdict?.flagged ?? []).map((f) => [f.order_id, f.rule]));
}

/** Drop the memoised verdicts. Tests only. */
export function resetVerdictCache(): void {
  cache.clear();
}
