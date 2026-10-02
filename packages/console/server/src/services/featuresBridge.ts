import type { SuiteFeatures, SuiteFeaturesOk, SuiteModuleFeature } from "@console/shared";
import { callConfigCli, type BridgeResult } from "./configBridge.js";

/**
 * What the suite has turned on, asked of the orchestrator (`configcli` op `features`) rather than
 * re-derived here. "Is earnings off?" folds the user's switch together with the machine's
 * capabilities (Dolt, Claude Code); that rule lives in Python, and a TypeScript copy reading the
 * config file directly would be a second answer free to drift from the one the scheduler acts on.
 *
 * Memoised for `TTL_MS` (one ~250 ms subprocess per window, failures included) and dropped the
 * moment the Config page saves or the halt toggles, so a module switched off in the browser leaves
 * the rail on the next poll rather than up to a window later.
 *
 * A failure is a value, never a throw: every consumer treats `ok: false` as "unknown", which means
 * VISIBLE. A broken bridge must cost a warning chip, not a blank console.
 */

export const TTL_MS = 15_000;

let memo: { at: number; value: SuiteFeatures } | null = null;

function boolMap(v: unknown): Record<string, boolean> {
  const out: Record<string, boolean> = {};
  if (v === null || typeof v !== "object" || Array.isArray(v)) return out;
  for (const [k, b] of Object.entries(v as Record<string, unknown>)) {
    if (typeof b === "boolean") out[k] = b;
  }
  return out;
}

/** The bridge's reply as the shared type. Anything malformed is a failure, never a guess at "off". */
export function parseFeatures(raw: BridgeResult): SuiteFeatures {
  if (!raw.ok) return { ok: false, error: raw.error };
  const modulesRaw = raw["modules"];
  if (modulesRaw === null || typeof modulesRaw !== "object" || Array.isArray(modulesRaw)) {
    return { ok: false, error: "features: the bridge reply has no modules map" };
  }
  const modules: Record<string, SuiteModuleFeature> = {};
  for (const [id, m] of Object.entries(modulesRaw as Record<string, unknown>)) {
    if (m === null || typeof m !== "object") continue;
    const r = m as Record<string, unknown>;
    // Only an explicit boolean `enabled` can hide a module; a module whose row is malformed is
    // left out, and an absent row reads as visible.
    if (typeof r["enabled"] !== "boolean") continue;
    modules[id] = {
      configured: r["configured"] === true,
      enabled: r["enabled"],
      missing: Array.isArray(r["missing"]) ? r["missing"].filter((x): x is string => typeof x === "string") : [],
    };
  }
  const ok: SuiteFeaturesOk = {
    ok: true,
    capabilities: boolMap(raw["capabilities"]),
    modules,
    services: boolMap(raw["services"]),
    features: boolMap(raw["features"]),
  };
  return ok;
}

export function getFeatures(now: number = Date.now()): SuiteFeatures {
  if (memo !== null && now - memo.at < TTL_MS) return memo.value;
  const value = parseFeatures(callConfigCli({ op: "features" }));
  memo = { at: now, value };
  return value;
}

/** Drop the memo: the Config page saved, or the halt flag moved. */
export function invalidateFeatures(): void {
  memo = null;
}

/**
 * Server-side twins of the web's `lib/visibility.ts` rules, for readers that filter rows before
 * they leave (the Overview's desk). Unknown is visible: a failed read, a missing features value
 * and an id the bridge does not name all keep the row.
 */
export function moduleOn(features: SuiteFeatures | undefined, id: string): boolean {
  if (features === undefined || !features.ok) return true;
  if (id === "gex") return features.services["gex-recorder"] !== false;
  return features.modules[id]?.enabled !== false;
}
