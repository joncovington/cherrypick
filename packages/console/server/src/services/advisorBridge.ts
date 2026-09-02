import { spawnSync } from "node:child_process";

/**
 * The console's only route to advisor state changes: the advisor's own CLI, as a subprocess
 * (`python -m cherrypick.advisor <verb>`, JSON out).
 *
 * Same shape and same reason as `configBridge.ts`. Killing an experiment has consequences beyond
 * flipping a column — it journals the reason, stops tonight's artifact being issued for it, and
 * lets a queued experiment take its slot — and all of that lives in one place in Python. A
 * TypeScript reimplementation would be a second copy of the lifecycle, free to disagree with the
 * one the scheduled runs use.
 *
 * Only two verbs are reachable from here, both narrowing: stop an experiment, dismiss a proposal.
 * There is deliberately no way to start, tune or enact anything from the browser.
 */

export type AdvisorOp = { op: "kill"; experimentId: string } | { op: "dismiss"; proposalId: number };

export interface AdvisorBridgeFailure {
  ok: false;
  error: string;
  code: "unavailable" | "not_found";
}

export type AdvisorBridgeOk = { ok: true } & Record<string, unknown>;
export type AdvisorBridgeResult = AdvisorBridgeOk | AdvisorBridgeFailure;

const UNAVAILABLE =
  "advisor bridge unavailable — the advisor package must be installed (pip install -e packages/advisor)";

function argvFor(op: AdvisorOp): string[] {
  return op.op === "kill"
    ? ["-m", "cherrypick.advisor", "kill", op.experimentId]
    : ["-m", "cherrypick.advisor", "dismiss", String(op.proposalId)];
}

/** What a run of the CLI produced. The seam sits HERE, at the process boundary, so the reply
 *  classification below is the real one in tests too. */
export interface AdvisorRun {
  status: number | null;
  stdout: string;
  stderr: string;
  /** Set when the process could not be run at all. */
  failure?: string;
}

function spawnCaller(op: AdvisorOp): AdvisorRun {
  try {
    const out = spawnSync("python", argvFor(op), {
      encoding: "utf-8",
      timeout: 20_000,
      windowsHide: true,
    });
    if (out.error !== undefined) {
      return { status: null, stdout: "", stderr: "", failure: out.error.message };
    }
    return { status: out.status, stdout: out.stdout ?? "", stderr: out.stderr ?? "" };
  } catch (err) {
    return { status: null, stdout: "", stderr: "", failure: (err as Error).message };
  }
}

let caller: (op: AdvisorOp) => AdvisorRun = spawnCaller;

/** Swap the subprocess out in tests. Pass nothing to restore the real one. */
export function setAdvisorCaller(fn?: (op: AdvisorOp) => AdvisorRun): void {
  caller = fn ?? spawnCaller;
}

export function callAdvisorCli(op: AdvisorOp): AdvisorBridgeResult {
  const run = caller(op);
  if (run.failure !== undefined) {
    return { ok: false, code: "unavailable", error: `${UNAVAILABLE} (${run.failure})` };
  }
  let parsed: Record<string, unknown>;
  try {
    parsed = JSON.parse(run.stdout.trim()) as Record<string, unknown>;
  } catch {
    const detail = run.stderr.trim().split(/\r?\n/).pop() ?? `exit ${String(run.status)}`;
    return { ok: false, code: "unavailable", error: `${UNAVAILABLE} — ${detail}` };
  }
  // The CLI exits non-zero on a refusal it can describe (an unknown id), and prints the reason in
  // the body either way. "That id does not exist" is a different thing for the page to say than
  // "the bridge is broken", so the two do not collapse into one error.
  if (parsed["ok"] !== true) {
    return {
      ok: false,
      code: "not_found",
      error: String(parsed["reason"] ?? parsed["error"] ?? "the advisor refused the request"),
    };
  }
  return parsed as AdvisorBridgeOk;
}
