import type { ChangeRow, ChangesModule, ModuleChanges } from "@console/shared";
import { AsyncMemo } from "../services/asyncMemo.js";
import { backtestResultsOn, getFeatures } from "../services/featuresBridge.js";
import { spawnModuleCliAsync, type SpawnedJson } from "../services/moduleCli.js";

/**
 * Every modification under test in one module (`python -m cherrypick.core.changes --module M`):
 * advised twins against their base, arms against their control, and for automations the backtest's
 * declared ideas and settings (`python -m cherrypick.backtest read declared`), passed to core as
 * `--declares`. A declared item from a licensed run is left out while licensed results are hidden, and
 * counted as `withheld`. Core reads, pairs and gives each row its reading; nothing is computed here.
 */

const UNAVAILABLE = "the changes read is unavailable — cherrypick-core must be installed (pip install -e packages/core)";
const DECLARED_UNAVAILABLE = "the backtest's declared items are unavailable";
// The comparison resamples every arm's sessions; its answer moves when a session settles.
const TTL_MS = 120_000;

type Caller = (argv: string[]) => SpawnedJson | Promise<SpawnedJson>;
const spawnCore: Caller = (argv) => spawnModuleCliAsync(["-m", "cherrypick.core.changes", ...argv], UNAVAILABLE, [0, 1], 60_000);
const spawnDeclared: Caller = () => spawnModuleCliAsync(["-m", "cherrypick.backtest", "read", "declared"], DECLARED_UNAVAILABLE);
let core: Caller = spawnCore;
let declaredCaller: Caller = spawnDeclared;
const memo = new AsyncMemo<SpawnedJson>();

/** Swap the subprocesses out in tests. Pass nothing to restore the real ones. */
export function setChangesCallers(fns?: { core?: Caller; declared?: Caller }): void {
  core = fns?.core ?? spawnCore;
  declaredCaller = fns?.declared ?? spawnDeclared;
  memo.clear();
}

async function declares(): Promise<{ items: Array<Record<string, unknown>>; withheld: number }> {
  const res = await declaredCaller([]);
  const items = res.ok && Array.isArray(res.json?.["items"]) ? (res.json!["items"] as Array<Record<string, unknown>>) : [];
  if (items.length === 0) return { items, withheld: 0 };
  // Fail closed: an item not plainly our own is read as licensed.
  const open = backtestResultsOn(await getFeatures());
  const shown = open ? items : items.filter((d) => d["licensed"] === false);
  return { items: shown, withheld: items.length - shown.length };
}

export async function readModuleChanges(module: ChangesModule, now: number = Date.now()): Promise<ModuleChanges> {
  const empty: ModuleChanges = { module, status: null, reason: null, min_sessions: null, counts: {}, rows: [], withheld: 0, error: null };
  const extra = module === "automations" ? await declares() : { items: [], withheld: 0 };
  const argv = ["--module", module, ...(extra.items.length ? ["--declares", JSON.stringify({ items: extra.items })] : [])];
  const res = await memo.get(argv.join(" "), TTL_MS, now, () => core(argv));
  if (!res.ok || res.json === null) return { ...empty, withheld: extra.withheld, error: res.ok ? "the changes read gave nothing" : res.error };
  const j = res.json;
  if (j["ok"] !== true) return { ...empty, withheld: extra.withheld, error: String(j["error"] ?? "the changes read failed") };
  return {
    module,
    status: typeof j["status"] === "string" ? j["status"] : null,
    reason: typeof j["reason"] === "string" ? j["reason"] : null,
    min_sessions: typeof j["min_sessions"] === "number" ? j["min_sessions"] : null,
    counts: (j["counts"] ?? {}) as ModuleChanges["counts"],
    rows: Array.isArray(j["rows"]) ? (j["rows"] as ChangeRow[]) : [],
    withheld: extra.withheld,
    error: null,
  };
}
