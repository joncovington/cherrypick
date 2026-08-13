import { spawnSync } from "node:child_process";

/**
 * The console's only route to suite config: the orchestrator's own editor, reached as a subprocess
 * (`python -m cherrypick.orchestrator.configcli`, JSON in / JSON out).
 *
 * This package holds NO config-writing logic of its own, deliberately. The guarded-pointer table
 * (which is what makes it impossible to arm or de-risk live trading from here), the byte-span
 * splicing that keeps a config's `_note`/`_header` documentation and key order intact, the
 * timestamped backup and the atomic write are live-safety properties that live in one place in
 * Python. A TypeScript reimplementation would be a second copy free to drift from the first.
 *
 * Same bridging pattern, and the same reason, as `auth/suiteBridge.ts`: the authority lives on the
 * Python side and Node asks it rather than reproducing it.
 */

export type BridgeCode = "guarded" | "conflict" | "invalid" | "not_found" | "bad_request" | "unavailable";

export interface BridgeFailure {
  ok: false;
  error: string;
  code: BridgeCode;
  /** Present on a guarded/not-found refusal: the pointer that was refused. */
  pointer?: string;
  issues?: Array<[string, string]>;
}

export type BridgeOk = { ok: true } & Record<string, unknown>;
export type BridgeResult = BridgeOk | BridgeFailure;

export type BridgeRequest =
  | { op: "targets" }
  | { op: "load"; target: string }
  | { op: "save"; target: string; expected_mtime?: number | null; edits: Array<{ pointer: string; value: unknown }> }
  | { op: "halt_status" }
  | { op: "set_halt"; present: boolean };

const UNAVAILABLE =
  "config bridge unavailable — the orchestrator package must be installed (pip install -e packages/orchestrator)";

function spawnCaller(req: BridgeRequest): BridgeResult {
  let out;
  try {
    out = spawnSync("python", ["-m", "cherrypick.orchestrator.configcli"], {
      input: JSON.stringify(req),
      encoding: "utf-8",
      timeout: 20_000,
      windowsHide: true,
    });
  } catch (err) {
    return { ok: false, code: "unavailable", error: `${UNAVAILABLE} (${(err as Error).message})` };
  }
  if (out.error !== undefined) {
    return { ok: false, code: "unavailable", error: `${UNAVAILABLE} (${out.error.message})` };
  }
  // A refusal rides in the body on status 0; a non-zero status means the bridge itself broke, which
  // is a different thing for the caller to say and a different thing to fix.
  if (out.status !== 0) {
    const detail = (out.stderr ?? "").trim().split(/\r?\n/).pop() ?? `exit ${String(out.status)}`;
    return { ok: false, code: "unavailable", error: `${UNAVAILABLE} — ${detail}` };
  }
  try {
    return JSON.parse(out.stdout.trim()) as BridgeResult;
  } catch {
    return { ok: false, code: "unavailable", error: `${UNAVAILABLE} — unparseable response` };
  }
}

let caller: (req: BridgeRequest) => BridgeResult = spawnCaller;

/** Swap the subprocess out in tests. Pass nothing to restore the real one. */
export function setBridgeCaller(fn?: (req: BridgeRequest) => BridgeResult): void {
  caller = fn ?? spawnCaller;
}

export function callConfigCli(req: BridgeRequest): BridgeResult {
  return caller(req);
}

/** The HTTP status a refusal maps to. Everything the client shows keys off this. */
export function statusForCode(code: BridgeCode): number {
  switch (code) {
    case "guarded":
      return 403;
    case "conflict":
      return 409;
    case "invalid":
      return 422;
    case "not_found":
      return 404;
    case "unavailable":
      return 502;
    default:
      return 400;
  }
}
