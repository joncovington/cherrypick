import type {
  BridgedResult,
  StudiesBoard,
  StudyGroup,
  StudyResult,
  StudyRival,
  StudyRow,
  StudyTask,
  StudyVerdict,
} from "@console/shared";
import { spawnModuleCliAsync, type SpawnedJson } from "./moduleCli.js";

/**
 * The Backtest page's studies board (`python -m cherrypick.cli hypotheses-board`; the orchestrator's
 * `hypotheses.board`, docs/studies-board-plan.md): each register row with its result files' verdicts
 * and its queue rows. Every rule is the orchestrator's: the group, the summary word, which status
 * wins. This renames the reply, drops a result file's full path for its name, and decides nothing.
 *
 * Memoised ~30 s, as the queue is. The caller is NOT the gate: the route refuses before reaching
 * here unless the suite says `backtest: true` (the result files are licensed).
 */

const UNAVAILABLE = "studies board read unavailable — the orchestrator must be installed";
const GROUPS: readonly StudyGroup[] = ["in_progress", "declared", "finished", "observation"];
const ARGV = ["-m", "cherrypick.cli", "hypotheses-board"];

let caller: () => Promise<SpawnedJson> = () => spawnModuleCliAsync(ARGV, UNAVAILABLE);

/** Swap the subprocess out in tests. Pass nothing to restore the real one. */
export function setStudiesCaller(fn?: () => Promise<SpawnedJson>): void {
  caller = fn ?? (() => spawnModuleCliAsync(ARGV, UNAVAILABLE));
}

export const TTL_MS = 30_000;
let cached: { at: number; value: Promise<SpawnedJson> } | null = null;

/** Drop the memoised response. Tests only. */
export function resetStudiesCache(): void {
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

function list(v: unknown): unknown[] {
  return Array.isArray(v) ? v : [];
}

function toVerdicts(v: unknown): StudyVerdict[] | null {
  if (!Array.isArray(v)) return null;
  const out: StudyVerdict[] = [];
  for (const x of v) {
    const r = rec(x);
    const verdict = s(r["verdict"]);
    if (verdict === null) return null;
    out.push({ arm: s(r["arm"]), verdict });
  }
  return out;
}

function toResult(v: unknown): StudyResult | null {
  const r = rec(v);
  const name = s(r["name"]);
  if (name === null) return null;
  const sp = r["span"];
  const span = Array.isArray(sp) && sp.length === 2 && typeof sp[0] === "string" && typeof sp[1] === "string" ? ([sp[0], sp[1]] as [string, string]) : null;
  return {
    name,
    present: r["present"] === true,
    modified: s(r["modified"]),
    window: s(r["window"]),
    span,
    runId: s(r["run_id"]),
    provisional: typeof r["provisional"] === "boolean" ? r["provisional"] : null,
    verdicts: toVerdicts(r["verdicts"]),
    summary: s(r["summary"]),
    notes: s(r["notes"]),
  };
}

function toTask(v: unknown): StudyTask | null {
  const r = rec(v);
  const id = n(r["id"]);
  const step = s(r["step"]);
  const status = s(r["status"]);
  if (id === null || step === null || status === null) return null;
  return { id, step, status, createdAt: s(r["created_at"]), startedAt: s(r["started_at"]), finishedAt: s(r["finished_at"]) };
}

function toRival(v: unknown): StudyRival | null {
  const r = rec(v);
  const explanation = s(r["explanation"]);
  const check = s(r["check"]);
  return explanation === null || check === null ? null : { explanation, check };
}

function toRow(v: unknown): StudyRow | null {
  const r = rec(v);
  const id = n(r["id"]);
  const study = s(r["study"]);
  const title = s(r["title"]);
  const status = s(r["status"]);
  const group = s(r["group"]) as StudyGroup | null;
  if (id === null || study === null || title === null || status === null || group === null || !GROUPS.includes(group)) return null;
  const queue = rec(r["queue"]);
  return {
    id,
    study,
    title,
    status,
    legacy: r["legacy"] === true,
    pointer: s(r["pointer"]),
    outcome: s(r["outcome"]),
    measure: s(r["measure"]),
    expected: s(r["expected"]),
    sample: s(r["sample"]),
    rule: s(r["rule"]),
    mechanism: s(r["mechanism"]),
    rivals: list(r["rivals"]).map(toRival).filter((x): x is StudyRival => x !== null),
    group,
    results: list(r["results"]).map(toResult).filter((x): x is StudyResult => x !== null),
    queueActive: queue["active"] === true,
    tasks: list(queue["tasks"]).map(toTask).filter((x): x is StudyTask => x !== null),
    lastActivity: s(r["last_activity"]),
  };
}

export function shapeStudies(res: SpawnedJson): BridgedResult<StudiesBoard> {
  if (!res.ok || res.json === null) return { ok: false, data: null, error: res.error ?? UNAVAILABLE };
  if (res.json["ok"] !== true) return { ok: false, data: null, error: String(res.json["error"] ?? UNAVAILABLE) };
  const rows = list(res.json["rows"]).map(toRow).filter((x): x is StudyRow => x !== null);
  const unregistered = list(res.json["unregistered"]).filter((x): x is string => typeof x === "string");
  return { ok: true, data: { rows, unregistered }, error: null };
}

export async function readStudies(now = Date.now()): Promise<BridgedResult<StudiesBoard>> {
  if (cached === null || now - cached.at >= TTL_MS) cached = { at: now, value: caller() };
  return shapeStudies(await cached.value);
}
