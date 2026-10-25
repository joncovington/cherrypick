import type { BridgedResult, ExecutionGap, ExecutionModule, ExecutionRecord, ExecutionWeek } from "@console/shared";
import { spawnModuleCliAsync, type SpawnedJson } from "./moduleCli.js";

/**
 * The Live page's execution tab (B10): `python -m cherrypick.cli execution --json`, the orchestrator's
 * `execution.py` over the live ledgers -- each live arm's fills against the mid at submit, fill rate,
 * overrides and the gap to its paper mirror, by week. Files only on the Python side; nothing here
 * touches an order. Every rule is the orchestrator's: this renames the reply and decides nothing.
 * Memoised ~60 s.
 */

const UNAVAILABLE = "execution record unavailable — the orchestrator must be installed";
const ARGV = ["-m", "cherrypick.cli", "execution", "--json"];

let caller: () => Promise<SpawnedJson> = () => spawnModuleCliAsync(ARGV, UNAVAILABLE);

/** Swap the subprocess out in tests. Pass nothing to restore the real one. */
export function setExecutionCaller(fn?: () => Promise<SpawnedJson>): void {
  caller = fn ?? (() => spawnModuleCliAsync(ARGV, UNAVAILABLE));
}

export const TTL_MS = 60_000;
let cached: { at: number; value: Promise<SpawnedJson> } | null = null;

export function resetExecutionCache(): void {
  cached = null;
}

function rec(v: unknown): Record<string, unknown> {
  return v && typeof v === "object" && !Array.isArray(v) ? (v as Record<string, unknown>) : {};
}
const s = (v: unknown): string | null => (typeof v === "string" && v !== "" ? v : null);
const n = (v: unknown): number | null => (typeof v === "number" && Number.isFinite(v) ? v : null);

function toWeek(v: unknown): ExecutionWeek | null {
  const r = rec(v);
  const week = s(r["week"]);
  if (week === null) return null;
  const overrides = (Array.isArray(r["overrides"]) ? r["overrides"] : []).map((o) => {
    const x = rec(o);
    return { kind: s(x["kind"]) ?? "override", session: s(x["session"]), at: s(x["at"]) };
  });
  return {
    week,
    net: n(r["net"]) ?? 0,
    trades: n(r["trades"]) ?? 0,
    orders: n(r["orders"]) ?? 0,
    fillRate: n(r["fill_rate"]),
    slipDollars: n(r["slip_dollars"]),
    slipPctWidthMean: n(r["slip_pct_width_mean"]),
    minutesToFillMean: n(r["minutes_to_fill_mean"]),
    overrides,
    clean: r["clean"] === true,
  };
}

function toGap(v: unknown): ExecutionGap | null {
  const r = rec(v);
  if (Object.keys(r).length === 0) return null;
  const parts: Record<string, number> = {};
  for (const [k, x] of Object.entries(rec(r["parts"]))) if (typeof x === "number") parts[k] = x;
  return { since: s(r["since"]), sessions: n(r["sessions"]) ?? 0, total: n(r["total"]), parts, largest: s(r["largest"]) };
}

export function shapeExecution(res: SpawnedJson): BridgedResult<ExecutionRecord> {
  if (!res.ok || res.json === null) return { ok: false, data: null, error: res.error ?? UNAVAILABLE };
  const modules: ExecutionModule[] = Object.entries(res.json).map(([module, v]) => {
    const r = rec(v);
    return {
      module,
      ok: r["ok"] === true,
      reason: s(r["reason"]),
      mirror: s(r["mirror"]),
      weeks: (Array.isArray(r["weeks"]) ? r["weeks"] : []).map(toWeek).filter((w): w is ExecutionWeek => w !== null),
      gap: toGap(r["gap"]),
    };
  });
  return { ok: true, data: { modules }, error: null };
}

export async function readExecution(now = Date.now()): Promise<BridgedResult<ExecutionRecord>> {
  if (cached === null || now - cached.at >= TTL_MS) cached = { at: now, value: caller() };
  return shapeExecution(await cached.value);
}
