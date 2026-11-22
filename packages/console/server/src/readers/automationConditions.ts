import type { AutomationConditions, ConditionsLabel } from "@console/shared";
import { AsyncMemo } from "../services/asyncMemo.js";
import { spawnModuleCliAsync, type SpawnedJson } from "../services/moduleCli.js";
import { shapeRegimeCuts } from "./regimeCuts.js";

/**
 * One automation's record in today's market conditions (`python -m cherrypick.automations.cli
 * conditions --name N`): its closed trades cut by each entry session's daily-bar trend and volatility
 * (`core.marketregime` over the stream cache's daily bars), assembled and stamped by `core.regimecuts`,
 * today's label set against each cut. The document is put in the regime cuts' shape
 * (`shapeRegimeCuts`), so MEIC's card renders it; nothing is computed here.
 */

const UNAVAILABLE =
  "today's conditions are unavailable — the automations package must be installed (pip install -e packages/automations)";
// Daily bars move once a session and the record when a structure closes.
const TTL_MS = 300_000;

type Caller = (name: string) => SpawnedJson | Promise<SpawnedJson>;
const spawnCli: Caller = (name) =>
  spawnModuleCliAsync(["-m", "cherrypick.automations.cli", "conditions", "--name", name], UNAVAILABLE, [0, 1], 60_000);
let caller: Caller = spawnCli;
const memo = new AsyncMemo<SpawnedJson>();

/** Swap the subprocess out in tests. Pass nothing to restore the real one. */
export function setAutomationConditionsCaller(fn?: Caller): void {
  caller = fn ?? spawnCli;
  memo.clear();
}

function labels(v: unknown): Record<string, ConditionsLabel> {
  if (v === null || typeof v !== "object" || Array.isArray(v)) return {};
  return Object.fromEntries(
    Object.entries(v as Record<string, unknown>).filter(([, l]) => l !== null && typeof l === "object" && !Array.isArray(l)),
  ) as Record<string, ConditionsLabel>;
}

export async function readAutomationConditionsFor(name: string, now: number = Date.now()): Promise<AutomationConditions> {
  const empty: AutomationConditions = { name, error: null, cuts: null, labels: {} };
  const res = await memo.get(name, TTL_MS, now, () => caller(name));
  if (!res.ok || res.json === null) return { ...empty, error: res.ok ? "today's conditions gave nothing" : res.error };
  const j = res.json;
  if (j["ok"] !== true) return { ...empty, error: String(j["error"] ?? "today's conditions failed") };
  const today = j["today"] !== null && typeof j["today"] === "object" ? (j["today"] as Record<string, unknown>) : {};
  return { name, error: null, cuts: shapeRegimeCuts(j), labels: labels(today["labels"]) };
}
