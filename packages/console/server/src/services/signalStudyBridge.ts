import type { BridgedResult, SignalStudyBrief, SignalStudyResult } from "@console/shared";
import { spawnModuleCliAsync, type SpawnedJson } from "./moduleCli.js";

/**
 * Compare → signals reads `packages/backtest`'s signal studies (`signalstudy.py`) through its own
 * read verbs, so the result files' layout and where they live (`paths.signals_dir`) stay the module's:
 *
 * - `read signals`: every result in brief, each marked `licensed` when it was computed over the
 *   vendor's store.
 * - `read signal --name N`: one result whole. An unknown name is the module's `{"ok": false}` with
 *   exit 1, passed on as that error.
 *
 * Read-only on the Python side; neither contacts the vendor. Memoised ~30 s.
 *
 * **This module is not the licence gate.** A licensed result may only be shown while the backtest
 * feature is on, and `routes/signals.ts` decides that before and after calling here.
 */

const UNAVAILABLE = "signal study read unavailable — cherrypick-backtest must be installed";

export type SignalStudyRequestKind = { kind: "list" } | { kind: "one"; name: string };

function spawnRequest(req: SignalStudyRequestKind): Promise<SpawnedJson> {
  if (req.kind === "list") return spawnModuleCliAsync(["-m", "cherrypick.backtest", "read", "signals"], UNAVAILABLE);
  return spawnModuleCliAsync(["-m", "cherrypick.backtest", "read", "signal", "--name", req.name], UNAVAILABLE, [0, 1]);
}

let caller: (req: SignalStudyRequestKind) => Promise<SpawnedJson> = spawnRequest;

/** Swap the subprocess out in tests. Pass nothing to restore the real one. */
export function setSignalStudyCaller(fn?: (req: SignalStudyRequestKind) => Promise<SpawnedJson>): void {
  caller = fn ?? spawnRequest;
}

export const TTL_MS = 30_000;
const cache = new Map<string, { at: number; value: Promise<SpawnedJson> }>();

/** Drop the memoised responses. Tests only. */
export function resetSignalStudyCache(): void {
  cache.clear();
}

function memo(req: SignalStudyRequestKind, now: number): Promise<SpawnedJson> {
  const key = req.kind === "list" ? "list" : `one|${req.name}`;
  const hit = cache.get(key);
  if (hit !== undefined && now - hit.at < TTL_MS) return hit.value;
  for (const [k, v] of cache) if (now - v.at >= TTL_MS) cache.delete(k);
  const value = caller(req);
  cache.set(key, { at: now, value });
  return value;
}

function refused(res: SpawnedJson): string | null {
  if (!res.ok || res.json === null) return res.error ?? UNAVAILABLE;
  if (res.json["ok"] !== true) return String(res.json["error"] ?? UNAVAILABLE);
  return null;
}

/** Every result in brief, before the route withholds any licensed one. */
export async function readSignalStudies(now = Date.now()): Promise<BridgedResult<{ studies: SignalStudyBrief[]; unreadable: string[] }>> {
  const res = await memo({ kind: "list" }, now);
  const error = refused(res);
  if (error !== null) return { ok: false, data: null, error };
  const studies = res.json?.["studies"];
  const unreadable = res.json?.["unreadable"];
  if (!Array.isArray(studies)) return { ok: false, data: null, error: `${UNAVAILABLE} — malformed reply` };
  return {
    ok: true,
    data: { studies: studies as SignalStudyBrief[], unreadable: Array.isArray(unreadable) ? (unreadable as string[]) : [] },
    error: null,
  };
}

/** One result. The caller has already checked `name` against `SIGNAL_STUDY_NAME`. */
export async function readSignalStudy(name: string, now = Date.now()): Promise<BridgedResult<SignalStudyResult>> {
  const res = await memo({ kind: "one", name }, now);
  const error = refused(res);
  if (error !== null) return { ok: false, data: null, error };
  const json = res.json as Record<string, unknown>;
  if (typeof json["licensed"] !== "boolean" || !Array.isArray(json["horizons"])) {
    return { ok: false, data: null, error: `${UNAVAILABLE} — malformed reply` };
  }
  return { ok: true, data: json as unknown as SignalStudyResult, error: null };
}
