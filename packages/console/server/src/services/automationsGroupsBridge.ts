import type { AutomationGroup, AutomationGroupsResult, AutomationVerdict } from "@console/shared";
import { AsyncMemo } from "./asyncMemo.js";
import { spawnModuleCliAsync, type SpawnedJson } from "./moduleCli.js";

/**
 * The automation groups, from the automations package itself (`python -m cherrypick.automations.cli
 * groups`): each group's switch and its automations with their research verdicts. The labels live
 * in the package's arms and its config's `groups` block, so this reads them there rather than keep a
 * second copy that could disagree. Read-only; memoised, since a group only changes when its config
 * is edited.
 */

const UNAVAILABLE =
  "automation groups unavailable — the automations package must be installed (pip install -e packages/automations)";
const TTL_MS = 60_000;

type Caller = () => SpawnedJson | Promise<SpawnedJson>;
const spawnCli: Caller = () => spawnModuleCliAsync(["-m", "cherrypick.automations.cli", "groups"], UNAVAILABLE);
let caller: Caller = spawnCli;
const memo = new AsyncMemo<SpawnedJson>();

/** Forget the memoised groups, after the Groups page saves a change. */
export function invalidateAutomationGroups(): void {
  memo.clear();
}

/** Swap the subprocess out in tests. Pass nothing to restore the real one. */
export function setAutomationsGroupsCaller(fn?: Caller): void {
  caller = fn ?? spawnCli;
  memo.clear();
}

const VERDICTS = new Set(["passed", "failed", "no_verdict"]);

function obj(v: unknown): Record<string, unknown> {
  return typeof v === "object" && v !== null && !Array.isArray(v) ? (v as Record<string, unknown>) : {};
}

/** The CLI's `{groups: {name: {enabled, arms: [{name, enabled, verdict}]}}}` as a sorted list. */
export function shapeGroups(json: Record<string, unknown> | null): AutomationGroup[] {
  const raw = obj(json?.["groups"]);
  return Object.keys(raw)
    .sort()
    .map((name) => {
      const g = obj(raw[name]);
      const members = Array.isArray(g["arms"]) ? g["arms"] : [];
      return {
        name,
        enabled: g["enabled"] !== false,
        automations: members.map((m) => {
          const r = obj(m);
          const verdict = typeof r["verdict"] === "string" && VERDICTS.has(r["verdict"]) ? (r["verdict"] as AutomationVerdict) : null;
          return { name: String(r["name"] ?? ""), enabled: r["enabled"] !== false, verdict };
        }),
      };
    });
}

export async function readAutomationGroups(now: number = Date.now()): Promise<AutomationGroupsResult> {
  const res = await memo.get("groups", TTL_MS, now, () => caller());
  if (!res.ok) return { groups: [], error: res.error };
  return { groups: shapeGroups(res.json), error: null };
}
