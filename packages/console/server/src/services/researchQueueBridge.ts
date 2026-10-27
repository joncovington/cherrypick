import type {
  BridgedResult,
  ResearchProgress,
  ResearchQueue,
  ResearchTask,
  ResearchTaskResult,
  ResearchTaskStatus,
} from "@console/shared";
import { spawnModuleCliAsync, type SpawnedJson } from "./moduleCli.js";

/**
 * The Backtest page's read of the research queue (`python -m cherrypick.cli queue-list`; the
 * orchestrator's `researchqueue.py`): the rows the supervisor's `research-queue` job runs, and why
 * each queued one waits. Read-only on the Python side; it adds, cancels and runs nothing. Every rule
 * is the orchestrator's: this renames the reply and decides nothing.
 *
 * Memoised ~30 s, as the backtest reads are. The caller is NOT the gate: the route refuses before
 * reaching here unless the suite says `backtest: true`.
 */

const UNAVAILABLE = "research queue read unavailable — the orchestrator must be installed";
const STATUSES: readonly ResearchTaskStatus[] = ["queued", "running", "done", "failed", "stopped", "cancelled"];

let caller: () => Promise<SpawnedJson> = () => spawnModuleCliAsync(["-m", "cherrypick.cli", "queue-list"], UNAVAILABLE);

/** Swap the subprocess out in tests. Pass nothing to restore the real one. */
export function setResearchQueueCaller(fn?: () => Promise<SpawnedJson>): void {
  caller = fn ?? (() => spawnModuleCliAsync(["-m", "cherrypick.cli", "queue-list"], UNAVAILABLE));
}

export const TTL_MS = 30_000;
let cached: { at: number; value: Promise<SpawnedJson> } | null = null;

/** Drop the memoised response. Tests only. */
export function resetResearchQueueCache(): void {
  cached = null;
}

function rec(v: unknown): Record<string, unknown> {
  return v && typeof v === "object" && !Array.isArray(v) ? (v as Record<string, unknown>) : {};
}

function s(v: unknown): string | null {
  return typeof v === "string" && v !== "" ? v : null;
}

function n(v: unknown): number | null {
  return typeof v === "number" && Number.isFinite(v) ? v : null;
}

function toResult(v: unknown): ResearchTaskResult | null {
  const r = rec(v);
  const log = s(r["log"]);
  if (log === null) return null;
  const report: Record<string, number | boolean> = {};
  for (const [k, x] of Object.entries(rec(r["report"]))) if (typeof x === "number" || typeof x === "boolean") report[k] = x;
  return {
    log,
    report: Object.keys(report).length > 0 ? report : null,
    input: s(r["input"]),
    store: s(r["store"]),
    output: s(r["output"]),
    outputExists: typeof r["output_exists"] === "boolean" ? r["output_exists"] : null,
  };
}

const PROGRESS_STATES = ["running", "done", "failed", "stalled", "orphaned"] as const;

/** A `core.progress` view, renamed; null for anything without a job name or a known state. */
export function toProgress(v: unknown): ResearchProgress | null {
  const r = rec(v);
  const job = s(r["job"]);
  const state = s(r["state"]) as ResearchProgress["state"] | null;
  if (job === null || state === null || !PROGRESS_STATES.includes(state)) return null;
  const phases = (Array.isArray(r["phases"]) ? r["phases"] : []).map((x) => {
    const p = rec(x);
    return {
      name: s(p["name"]) ?? "",
      done: n(p["done"]) ?? 0,
      total: n(p["total"]),
      unit: s(p["unit"]) ?? "",
      current: s(p["current"]),
    };
  });
  return {
    file: s(r["file"]) ?? job,
    job,
    state,
    phases,
    fraction: n(r["fraction"]),
    etaSeconds: n(r["eta_s"]),
    ageSeconds: n(r["age_s"]),
    line: s(r["line"]) ?? "",
    note: s(r["note"]),
    startedAt: n(r["started_at"]),
    updatedAt: n(r["updated_at"]),
  };
}

function toTask(v: unknown): ResearchTask | null {
  const r = rec(v);
  const id = n(r["id"]);
  const kind = s(r["kind"]);
  const study = s(r["study"]);
  const step = s(r["step"]);
  const status = s(r["status"]) as ResearchTaskStatus | null;
  if (id === null || kind === null || study === null || step === null || status === null || !STATUSES.includes(status)) return null;
  return {
    id,
    study,
    step,
    name: `${study}/${step}`,
    kind,
    output: s(r["output"]),
    result: toResult(r["result"]),
    args: rec(r["args"]),
    needs: s(r["needs"]) ?? "",
    notBefore: s(r["not_before"]),
    stopBy: s(r["stop_by"]),
    status,
    createdAt: s(r["created_at"]) ?? "",
    startedAt: s(r["started_at"]),
    finishedAt: s(r["finished_at"]),
    runs: n(r["runs"]) ?? 0,
    exitCode: n(r["exit_code"]),
    note: s(r["note"]),
    waits: s(r["waits"]),
    progress: toProgress(r["progress"]),
  };
}

export function shapeQueue(res: SpawnedJson): BridgedResult<ResearchQueue> {
  if (!res.ok || res.json === null) return { ok: false, data: null, error: res.error ?? UNAVAILABLE };
  if (res.json["ok"] !== true) return { ok: false, data: null, error: String(res.json["error"] ?? UNAVAILABLE) };
  const b = res.json["blackout"];
  const blackout = Array.isArray(b) && b.length === 2 && b.every((x) => typeof x === "string") ? ([b[0], b[1]] as [string, string]) : null;
  const tasks = (Array.isArray(res.json["tasks"]) ? res.json["tasks"] : []).map(toTask).filter((t): t is ResearchTask => t !== null);
  const byHand = (Array.isArray(res.json["by_hand"]) ? res.json["by_hand"] : [])
    .map(toProgress)
    .filter((p): p is ResearchProgress => p !== null);
  return { ok: true, data: { sessionHeld: res.json["session_held"] === true, blackout, tasks, byHand }, error: null };
}

export async function readResearchQueue(now = Date.now()): Promise<BridgedResult<ResearchQueue>> {
  if (cached === null || now - cached.at >= TTL_MS) cached = { at: now, value: caller() };
  return shapeQueue(await cached.value);
}
