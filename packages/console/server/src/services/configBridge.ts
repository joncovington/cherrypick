import { suitePython } from "@console/shared/paths";
import { spawn } from "node:child_process";

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
  | { op: "set_halt"; present: boolean }
  | { op: "features" };

const UNAVAILABLE =
  "config bridge unavailable — the orchestrator package must be installed (pip install -e packages/orchestrator)";

const TIMEOUT_MS = 20_000;

/**
 * One configcli call, off the event loop: a synchronous spawn held every request and the heartbeat
 * for its ~0.5 s, once per target on the Config page (six in a row, ~3 s, 2026-11-02).
 */
function spawnCaller(req: BridgeRequest): Promise<BridgeResult> {
  return new Promise((resolve) => {
    const unavailable = (detail: string): void =>
      resolve({ ok: false, code: "unavailable", error: `${UNAVAILABLE} ${detail}` });
    let child;
    try {
      child = spawn(suitePython(), ["-m", "cherrypick.orchestrator.configcli"], { windowsHide: true });
    } catch (err) {
      unavailable(`(${(err as Error).message})`);
      return;
    }
    let stdout = "";
    let stderr = "";
    child.stdout.setEncoding("utf-8").on("data", (d: string) => (stdout += d));
    child.stderr.setEncoding("utf-8").on("data", (d: string) => (stderr += d));
    const timer = setTimeout(() => child.kill(), TIMEOUT_MS);
    child.on("error", (err) => {
      clearTimeout(timer);
      unavailable(`(${err.message})`);
    });
    child.on("close", (status) => {
      clearTimeout(timer);
      // A refusal rides in the body on status 0; a non-zero status means the bridge itself broke,
      // which is a different thing for the caller to say and a different thing to fix.
      if (status !== 0) {
        unavailable(`— ${stderr.trim().split(/\r?\n/).pop() || `exit ${String(status)}`}`);
        return;
      }
      try {
        resolve(JSON.parse(stdout.trim()) as BridgeResult);
      } catch {
        unavailable("— unparseable response");
      }
    });
    child.stdin.on("error", () => undefined); // a child that dies early reports through `close`
    child.stdin.end(JSON.stringify(req));
  });
}

type Caller = (req: BridgeRequest) => BridgeResult | Promise<BridgeResult>;
let caller: Caller = spawnCaller;

/** Swap the subprocess out in tests. Pass nothing to restore the real one. */
export function setBridgeCaller(fn?: Caller): void {
  caller = fn ?? spawnCaller;
}

export async function callConfigCli(req: BridgeRequest): Promise<BridgeResult> {
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
