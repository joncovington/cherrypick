import type { PnlTrace } from "@console/shared";
import { spawnModuleCliAsync } from "./moduleCli.js";

/**
 * One position's entry and management trace, read from `python -m cherrypick.core.trace read`: a
 * bridge, not a mirror. Which attempt filled a position, the runs before it and the management runs
 * are all decided in core; nothing here re-derives one. Not memoised: it runs on a click, for one
 * closed position, which never changes.
 */

const UNAVAILABLE = "trace unavailable — cherrypick-core must be installed (pip install -e packages/core)";

async function spawnTrace(dbPath: string, schema: string, positionId: string): Promise<PnlTrace> {
  const argv = ["-m", "cherrypick.core.trace", "read", "--db", dbPath, "--schema", schema];
  argv.push("--position-id", positionId);
  const res = await spawnModuleCliAsync(argv, UNAVAILABLE);
  if (!res.ok || res.json === null) return { ok: false, error: res.error };
  if (res.json["ok"] !== true) {
    const err = res.json["error"];
    return { ok: false, error: typeof err === "string" ? err : `${UNAVAILABLE} — refused` };
  }
  return res.json as unknown as PnlTrace;
}

let caller = spawnTrace;

/** Swap the subprocess out in tests. Pass nothing to restore the real one. */
export function setTraceCaller(fn?: typeof spawnTrace): void {
  caller = fn ?? spawnTrace;
}

export function readTraceAsync(dbPath: string, schema: string, positionId: string): Promise<PnlTrace> {
  return caller(dbPath, schema, positionId);
}
