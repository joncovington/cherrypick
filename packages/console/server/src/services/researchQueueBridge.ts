import type { BridgedResult, ResearchQueue, ResearchTask, ResearchTaskStatus } from "@console/shared";
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

function toTask(v: unknown): ResearchTask | null {
  const r = rec(v);
  const id = n(r["id"]);
  const kind = s(r["kind"]);
  const status = s(r["status"]) as ResearchTaskStatus | null;
  if (id === null || kind === null || status === null || !STATUSES.includes(status)) return null;
  return {
    id,
    kind,
    label: s(r["label"]),
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
  };
}

export function shapeQueue(res: SpawnedJson): BridgedResult<ResearchQueue> {
  if (!res.ok || res.json === null) return { ok: false, data: null, error: res.error ?? UNAVAILABLE };
  if (res.json["ok"] !== true) return { ok: false, data: null, error: String(res.json["error"] ?? UNAVAILABLE) };
  const b = res.json["blackout"];
  const blackout = Array.isArray(b) && b.length === 2 && b.every((x) => typeof x === "string") ? ([b[0], b[1]] as [string, string]) : null;
  const tasks = (Array.isArray(res.json["tasks"]) ? res.json["tasks"] : []).map(toTask).filter((t): t is ResearchTask => t !== null);
  return { ok: true, data: { sessionHeld: res.json["session_held"] === true, blackout, tasks }, error: null };
}

export async function readResearchQueue(now = Date.now()): Promise<BridgedResult<ResearchQueue>> {
  if (cached === null || now - cached.at >= TTL_MS) cached = { at: now, value: caller() };
  return shapeQueue(await cached.value);
}
