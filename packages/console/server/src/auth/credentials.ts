import { Entry } from "@napi-rs/keyring";
import { readSuiteOauthEntries } from "./suiteBridge.js";

/**
 * SINGLE SOURCE of broker auth: the suite's canonical keyring entries
 * (`production:client_secret` / `production:refresh_token` under the
 * `cherrypick-broker` service) — the same entries every Python module reads
 * through and onboarding writes. The console reads them via the Python
 * bridge (Python keyring targets aren't addressable from the Node keyring)
 * and `credentials set` writes THOSE entries, so there is one credential set
 * however it is managed.
 *
 * The Node-side slots from the pre-unification era (`oauth` under
 * cherrypick-broker / cherrypick-console) remain as read-only fallbacks and
 * are what `credentials clear` removes; the suite entries are never deleted
 * from here — onboarding owns their lifecycle.
 *
 * Scope ("read" | "trade") is detected by a live probe and cached in memory
 * per process — never persisted, so it can't go stale against a rotated
 * refresh token.
 */
const LEGACY_SLOTS: Array<[string, string]> = [
  ["cherrypick-broker", "oauth"],
  ["cherrypick-console", "oauth"],
];

export interface ConsoleCredentials {
  clientSecret: string;
  refreshToken: string;
  source: "suite" | "legacy-slot";
}

let cached: ConsoleCredentials | null | undefined;
let currentScope: "read" | "trade" | null = null;
let scopeValidatedAt: string | null = null;

export function getScope(): { scope: "read" | "trade" | null; validatedAt: string | null } {
  return { scope: currentScope, validatedAt: scopeValidatedAt };
}

export function setScope(scope: "read" | "trade"): void {
  currentScope = scope;
  scopeValidatedAt = new Date().toISOString();
}

/** Drop the per-process caches (after `credentials set` rotates the entries). */
export function resetCredentialCache(): void {
  cached = undefined;
  currentScope = null;
  scopeValidatedAt = null;
}

function readLegacySlots(): ConsoleCredentials | null {
  for (const [service, account] of LEGACY_SLOTS) {
    try {
      const raw = new Entry(service, account).getPassword();
      if (raw === null) continue;
      const parsed = JSON.parse(raw) as { clientSecret?: unknown; refreshToken?: unknown };
      if (typeof parsed.clientSecret === "string" && typeof parsed.refreshToken === "string") {
        return { clientSecret: parsed.clientSecret, refreshToken: parsed.refreshToken, source: "legacy-slot" };
      }
    } catch {
      /* try the next slot */
    }
  }
  return null;
}

export function loadCredentials(): ConsoleCredentials | null {
  if (cached !== undefined) return cached;
  const suite = readSuiteOauthEntries();
  if (suite !== null) {
    cached = { ...suite, source: "suite" };
    return cached;
  }
  cached = readLegacySlots();
  return cached;
}

/** Remove the console-era slots only. The suite entries are onboarding's to manage. */
export function clearLegacySlots(): boolean {
  let cleared = false;
  for (const [service, account] of LEGACY_SLOTS) {
    try {
      if (new Entry(service, account).deletePassword()) cleared = true;
    } catch {
      /* absent */
    }
  }
  resetCredentialCache();
  return cleared;
}

/** "abcd…wxyz" — enough to recognize a value without revealing it. */
export function mask(value: string): string {
  if (value.length <= 8) return "•".repeat(value.length);
  return `${value.slice(0, 4)}…${value.slice(-4)}`;
}
