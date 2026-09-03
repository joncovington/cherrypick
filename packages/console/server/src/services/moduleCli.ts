import { spawnSync } from "node:child_process";

/**
 * The subprocess mechanics every module-CLI bridge in this package shares.
 *
 * `calendarsBridge.ts`, `screenBridge.ts` and `metricsBridge.ts` each carried their own copy of
 * this exact sequence -- spawn python with a timeout, distinguish a spawn failure from a nonzero
 * exit from an unparseable response, and fold all three into one `{ok, json, error}` shape --
 * differing only in the argv they built and the "package not installed" message they reported.
 * Extracted 2026-09 once three copies existed and were compared body-for-body (this repo's own
 * dedup rule: normalise the varying identifier -- here, argv and the message -- before folding).
 *
 * Each bridge still owns its own argv construction and its own shaping of the raw JSON into a
 * typed result -- only the spawn-and-parse mechanics are shared. A bridge's own `caller`
 * indirection (the thing its tests swap out) stays at the bridge's own typed-result level, not
 * here, so no existing bridge's test-mocking surface changes.
 */

export interface SpawnedJson {
  ok: boolean;
  json: Record<string, unknown> | null;
  error: string | null;
}

/** Spawn `python <argv>`, parse stdout as JSON. `unavailableMessage` names the package this argv
 * needs installed, and prefixes every failure branch -- a spawn failure, a nonzero exit (its
 * detail is the last stderr line), or a response that didn't parse as JSON. */
export function spawnModuleCli(argv: string[], unavailableMessage: string): SpawnedJson {
  let out;
  try {
    out = spawnSync("python", argv, { encoding: "utf-8", timeout: 30_000, windowsHide: true });
  } catch (err) {
    return { ok: false, json: null, error: `${unavailableMessage} (${(err as Error).message})` };
  }
  if (out.error !== undefined) {
    return { ok: false, json: null, error: `${unavailableMessage} (${out.error.message})` };
  }
  if (out.status !== 0) {
    const detail = (out.stderr ?? "").trim().split(/\r?\n/).pop() ?? `exit ${String(out.status)}`;
    return { ok: false, json: null, error: `${unavailableMessage} — ${detail}` };
  }
  try {
    return { ok: true, json: JSON.parse(out.stdout.trim()) as Record<string, unknown>, error: null };
  } catch {
    return { ok: false, json: null, error: `${unavailableMessage} — unparseable response` };
  }
}
