import { suitePython } from "@console/shared";
import { execFile, spawnSync } from "node:child_process";

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

/** The `error` of a `{"ok": false, "error": "..."}` document on stdout, else null. */
function stdoutError(stdout: string | null | undefined): string | null {
  try {
    const doc = JSON.parse((stdout ?? "").trim()) as unknown;
    if (typeof doc === "object" && doc !== null && (doc as Record<string, unknown>)["ok"] === false) {
      const err = (doc as Record<string, unknown>)["error"];
      return typeof err === "string" && err !== "" ? err : null;
    }
  } catch {
    // not JSON: the caller falls back to stderr
  }
  return null;
}

/** Spawn `python <argv>`, parse stdout as JSON. `unavailableMessage` names the package this argv
 * needs installed, and prefixes every failure branch -- a spawn failure, a nonzero exit (its
 * detail is the CLI's own `{"ok": false, "error"}` from stdout where it wrote one, else the last
 * stderr line), or a response that didn't parse as JSON. `okStatuses` names the exit codes that
 * still carry an answer on stdout (`core.impliedvar check` exits 1 for a check that did not agree,
 * with the reading printed); every other nonzero exit is a failure. */
export function spawnModuleCli(argv: string[], unavailableMessage: string, okStatuses: readonly number[] = [0]): SpawnedJson {
  let out;
  try {
    out = spawnSync(suitePython(), argv, { encoding: "utf-8", timeout: 30_000, windowsHide: true });
  } catch (err) {
    return { ok: false, json: null, error: `${unavailableMessage} (${(err as Error).message})` };
  }
  if (out.error !== undefined) {
    return { ok: false, json: null, error: `${unavailableMessage} (${out.error.message})` };
  }
  return parseOutcome(out.status, out.stdout, out.stderr, unavailableMessage, okStatuses);
}

/** The shared reading of a finished CLI run: exit status, stdout and stderr into `SpawnedJson`. One
 *  implementation, so the sync and async spawners can never disagree about what failed. */
function parseOutcome(
  status: number | null,
  stdout: string | null | undefined,
  stderr: string | null | undefined,
  unavailableMessage: string,
  okStatuses: readonly number[],
): SpawnedJson {
  if (status === null || !okStatuses.includes(status)) {
    // A CLI that reports its own failure as `{"ok": false, "error": ...}` on stdout and exits 1
    // (core.compare does) leaves stderr empty, so its reason is only in stdout; without this the
    // page would say "exit 1" where the module had said what broke.
    const detail = stdoutError(stdout) ?? ((stderr ?? "").trim().split(/\r?\n/).pop() || `exit ${String(status)}`);
    return { ok: false, json: null, error: `${unavailableMessage} — ${detail}` };
  }
  try {
    return { ok: true, json: JSON.parse((stdout ?? "").trim()) as Record<string, unknown>, error: null };
  } catch {
    return { ok: false, json: null, error: `${unavailableMessage} — unparseable response` };
  }
}

/**
 * `spawnModuleCli` without blocking the event loop: the same argv, timeout and failure shapes, as a
 * promise. A synchronous spawn holds the whole server -- every request and the heartbeat -- for as
 * long as the subprocess runs, which is fine for a one-off read and not for a page that reads every
 * module (Compare's risk tab, eight analyses: ~6 s cold, serial). Never rejects: every failure is a
 * `{ok: false}` answer, as the sync spawner's are.
 */
export function spawnModuleCliAsync(
  argv: string[],
  unavailableMessage: string,
  okStatuses: readonly number[] = [0],
): Promise<SpawnedJson> {
  return new Promise((resolve) => {
    try {
      execFile(
        suitePython(),
        argv,
        { encoding: "utf-8", timeout: 30_000, windowsHide: true, maxBuffer: 64 * 1024 * 1024 },
        (err, stdout, stderr) => {
          // execFile reports a nonzero exit as `err` with `code`; a spawn failure has no numeric code.
          const code = err === null ? 0 : typeof (err as { code?: unknown }).code === "number" ? (err as { code: number }).code : null;
          if (err !== null && code === null) {
            resolve({ ok: false, json: null, error: `${unavailableMessage} (${err.message})` });
            return;
          }
          resolve(parseOutcome(code, stdout, stderr, unavailableMessage, okStatuses));
        },
      );
    } catch (err) {
      resolve({ ok: false, json: null, error: `${unavailableMessage} (${(err as Error).message})` });
    }
  });
}
