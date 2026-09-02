import { useEffect, useSyncExternalStore } from "react";
import { getCsrf } from "./api";

/**
 * The console's own display preferences.
 *
 * The store on the server is the source of truth — it is what makes a preference follow you to the
 * desktop shell — but a preference that only arrives after a fetch is useless for deciding what the
 * FIRST render looks like. Defaulting the paper/live toggle is the sharp case: reading it late means
 * the page paints paper, then flips to live a moment later, which on a trading surface is worse than
 * not having the preference at all.
 *
 * So localStorage is a synchronous mirror, hydrated at import and written through on every change,
 * and the server copy refreshes it in the background. Same idea as the card-collapse state, which
 * already lives in localStorage for the same reason.
 */

const LS_KEY = "cherrypick-console-prefs-v1";

let cache: Record<string, unknown> = (() => {
  try {
    return JSON.parse(localStorage.getItem(LS_KEY) ?? "{}") as Record<string, unknown>;
  } catch {
    return {};
  }
})();

let version = 0;
const listeners = new Set<() => void>();

function publish(next: Record<string, unknown>): void {
  cache = next;
  try {
    localStorage.setItem(LS_KEY, JSON.stringify(next));
  } catch {
    /* private mode or a full quota — the server copy still holds */
  }
  version += 1;
  for (const l of listeners) l();
}

/** Synchronous read, safe during the first render. */
export function getPref(key: string): unknown {
  return cache[key];
}

export function getBoolPref(key: string): boolean {
  return cache[key] === true;
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export function useBoolPref(key: string): boolean {
  useSyncExternalStore(subscribe, () => version);
  return cache[key] === true;
}

export function usePrefsVersion(): number {
  return useSyncExternalStore(subscribe, () => version);
}

/** Write through: the local mirror updates immediately, the server copy follows. */
export async function writePref(key: string, value: unknown): Promise<void> {
  publish({ ...cache, [key]: value });
  try {
    const res = await fetch("/api/config/prefs", {
      method: "POST",
      headers: { "content-type": "application/json", "x-csrf-token": await getCsrf() },
      body: JSON.stringify({ key, value }),
    });
    if (res.ok) {
      const body = (await res.json()) as { prefs?: Record<string, unknown> };
      if (body.prefs !== undefined) publish(body.prefs);
    }
  } catch {
    // The local mirror already reflects the change; the next sync reconciles it.
  }
}

/** Pull the server copy once per session and reconcile the mirror with it. */
export function usePrefsSync(): void {
  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const res = await fetch("/api/config/prefs");
        if (!res.ok) return;
        const body = (await res.json()) as { prefs?: Record<string, unknown> };
        if (!cancelled && body.prefs !== undefined) publish(body.prefs);
      } catch {
        /* offline console still runs off the mirror */
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);
}
