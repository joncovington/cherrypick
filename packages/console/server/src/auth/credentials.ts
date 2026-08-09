import { Entry } from "@napi-rs/keyring";

/**
 * The console's own broker credential — a dedicated OAuth grant scoped to reads,
 * stored in the OS credential store under its own service name so it never
 * touches the Python side's keyring entries.
 */
const SERVICE = "cherrypick-console";
const ACCOUNT = "oauth";

export interface ConsoleCredentials {
  clientSecret: string;
  refreshToken: string;
}

function entry(): Entry {
  return new Entry(SERVICE, ACCOUNT);
}

export function saveCredentials(creds: ConsoleCredentials): void {
  entry().setPassword(JSON.stringify(creds));
}

export function loadCredentials(): ConsoleCredentials | null {
  try {
    const raw = entry().getPassword();
    if (raw === null) return null;
    const parsed = JSON.parse(raw) as Partial<ConsoleCredentials>;
    if (typeof parsed.clientSecret !== "string" || typeof parsed.refreshToken !== "string") {
      return null;
    }
    return { clientSecret: parsed.clientSecret, refreshToken: parsed.refreshToken };
  } catch {
    return null;
  }
}

export function clearCredentials(): boolean {
  try {
    return entry().deletePassword();
  } catch {
    return false;
  }
}

/** "abcd…wxyz" — enough to recognize a value without revealing it. */
export function mask(value: string): string {
  if (value.length <= 8) return "•".repeat(value.length);
  return `${value.slice(0, 4)}…${value.slice(-4)}`;
}
