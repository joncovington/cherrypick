import type { PnlExcursion } from "@console/shared";
import { spawnModuleCliAsync } from "./moduleCli.js";

/**
 * One position's excursion, read from `python -m cherrypick.core.excursions read`: a bridge, not a
 * mirror. The path is built in core from the module's own legs and marks, on the basis the module
 * records, and its endpoint is checked against the recorded gross there; nothing here re-derives a
 * point. Not memoised: it runs on a click, for one closed position, which never changes.
 */

const UNAVAILABLE = "excursions unavailable — cherrypick-core must be installed (pip install -e packages/core)";

async function spawnExcursion(dbPath: string, schema: string, positionId: string): Promise<PnlExcursion> {
  const argv = ["-m", "cherrypick.core.excursions", "read", "--db", dbPath, "--schema", schema];
  argv.push("--position-id", positionId);
  const res = await spawnModuleCliAsync(argv, UNAVAILABLE);
  if (!res.ok || res.json === null) return { ok: false, error: res.error };
  if (res.json["ok"] !== true) {
    const err = res.json["error"];
    return { ok: false, error: typeof err === "string" ? err : `${UNAVAILABLE} — refused` };
  }
  return res.json as unknown as PnlExcursion;
}

let caller = spawnExcursion;

/** Swap the subprocess out in tests. Pass nothing to restore the real one. */
export function setExcursionCaller(fn?: typeof spawnExcursion): void {
  caller = fn ?? spawnExcursion;
}

export function readExcursionAsync(dbPath: string, schema: string, positionId: string): Promise<PnlExcursion> {
  return caller(dbPath, schema, positionId);
}
