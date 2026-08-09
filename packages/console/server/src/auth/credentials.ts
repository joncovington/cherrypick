import { Entry } from "@napi-rs/keyring";

/**
 * THE suite broker credential — one credential set for the whole suite,
 * stored under the suite's own keyring service (`cherrypick-broker`) in a
 * dedicated `oauth` slot: the OAuth application's shared client secret plus
 * a refresh token. Scope rides on the refresh token; `scope` records what a
 * live probe detected ("read" = write-oriented functions disable themselves).
 * The pre-unification `cherrypick-console` slot is migrated on first read.
 */
const SERVICE = "cherrypick-broker";
const ACCOUNT = "oauth";
const LEGACY_SERVICE = "cherrypick-console";

export interface ConsoleCredentials {
  clientSecret: string;
  refreshToken: string;
  /** Detected by the live probe on `credentials set`; absent = never probed. */
  scope?: "read" | "trade";
  validatedAt?: string;
}

function entry(): Entry {
  return new Entry(SERVICE, ACCOUNT);
}

export function saveCredentials(creds: ConsoleCredentials): void {
  entry().setPassword(JSON.stringify(creds));
}

function parse(raw: string | null): ConsoleCredentials | null {
  if (raw === null) return null;
  try {
    const parsed = JSON.parse(raw) as Partial<ConsoleCredentials>;
    if (typeof parsed.clientSecret !== "string" || typeof parsed.refreshToken !== "string") return null;
    return {
      clientSecret: parsed.clientSecret,
      refreshToken: parsed.refreshToken,
      scope: parsed.scope === "read" || parsed.scope === "trade" ? parsed.scope : undefined,
      validatedAt: typeof parsed.validatedAt === "string" ? parsed.validatedAt : undefined,
    };
  } catch {
    return null;
  }
}

export function loadCredentials(): ConsoleCredentials | null {
  try {
    const current = parse(entry().getPassword());
    if (current !== null) return current;
  } catch {
    /* fall through to legacy */
  }
  // Migrate the pre-unification slot: copy into the suite-wide service.
  try {
    const legacy = parse(new Entry(LEGACY_SERVICE, ACCOUNT).getPassword());
    if (legacy !== null) {
      saveCredentials(legacy);
      return legacy;
    }
  } catch {
    /* no legacy entry either */
  }
  return null;
}

export function clearCredentials(): boolean {
  let cleared = false;
  try {
    cleared = entry().deletePassword();
  } catch {
    /* absent */
  }
  try {
    new Entry(LEGACY_SERVICE, ACCOUNT).deletePassword();
  } catch {
    /* absent */
  }
  return cleared;
}

/** "abcd…wxyz" — enough to recognize a value without revealing it. */
export function mask(value: string): string {
  if (value.length <= 8) return "•".repeat(value.length);
  return `${value.slice(0, 4)}…${value.slice(-4)}`;
}
