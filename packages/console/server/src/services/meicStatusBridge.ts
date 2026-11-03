import type { LiveMeicCritical, LiveMeicReadiness } from "@console/shared";
import { AsyncMemo } from "./asyncMemo.js";
import { spawnModuleCliAsync, type SpawnedJson } from "./moduleCli.js";

/**
 * MEIC's live readiness, read from the module that owns it (`live_loop --status`: `arm_ready`,
 * `open_ics`, `criticals_today`). Whether live may be armed is a derivation over the gates, the
 * ledger, the broker-hold files, the supervisor heartbeat and the kill criteria, so the console asks
 * `arm_verdict` rather than keeping a second copy that could disagree with `/live-meic-start`.
 * `--status` reads files and the ledger only, never the broker, and needs no arm.
 *
 * A failed or malformed answer is `{ok: false, error}`, never a verdict: the card must not read a
 * dead bridge as "ready".
 */

const UNAVAILABLE = "MEIC status unavailable — the meic package must be installed (pip install -e packages/meic)";

const strings = (v: unknown): string[] | null =>
  Array.isArray(v) && v.every((x) => typeof x === "string") ? (v as string[]) : null;

/** The module's `--status` document into the console's readiness, or a failure naming what is wrong. */
export function shapeReadiness(res: SpawnedJson): LiveMeicReadiness {
  if (!res.ok || res.json === null) return { ok: false, error: res.error ?? "no answer" };
  const doc = res.json;
  if (doc["ok"] === false) return { ok: false, error: typeof doc["error"] === "string" ? doc["error"] : "status refused" };
  const verdict = doc["arm_ready"] as Record<string, unknown> | null | undefined;
  const blockers = strings(verdict?.["blockers"]);
  const warnings = strings(verdict?.["warnings"]);
  if (typeof verdict !== "object" || verdict === null || typeof verdict["ready"] !== "boolean" || blockers === null || warnings === null) {
    return { ok: false, error: "MEIC status has no arm_ready verdict (the meic package may be out of date)" };
  }
  const openIcs = strings(doc["open_ics"]) ?? [];
  const criticals: LiveMeicCritical[] = Array.isArray(doc["criticals_today"])
    ? (doc["criticals_today"] as unknown[]).flatMap((c) => {
        const r = c as Record<string, unknown> | null;
        return r !== null && typeof r === "object" && typeof r["message"] === "string"
          ? [{ at: typeof r["at"] === "string" ? r["at"] : "", message: r["message"] }]
          : [];
      })
    : [];
  // A verdict that says ready while it lists blockers contradicts itself: take the stricter reading.
  return { ok: true, ready: verdict["ready"] && blockers.length === 0, blockers, warnings, openIcs, criticals };
}

async function spawnCaller(): Promise<SpawnedJson> {
  return spawnModuleCliAsync(["-m", "cherrypick.meic.live_loop", "--status"], UNAVAILABLE);
}

type Caller = () => SpawnedJson | Promise<SpawnedJson>;
let caller: Caller = spawnCaller;

/** Swap the subprocess out in tests. Pass nothing to restore the real one. */
export function setMeicStatusCaller(fn?: Caller): void {
  caller = fn ?? spawnCaller;
}

// The Live page polls every 15 s; one subprocess per ~30 s is plenty for a readout a person reads.
const TTL_MS = 30_000;
const cache = new AsyncMemo<LiveMeicReadiness>();

export function readMeicReadiness(now = Date.now()): Promise<LiveMeicReadiness> {
  return cache.get("status", TTL_MS, now, async () => shapeReadiness(await caller()));
}

/** Drop the memoised readout. Tests only. */
export function resetMeicStatusCache(): void {
  cache.clear();
}
