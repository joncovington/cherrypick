import type { GroupBudgetFromBacktest, GroupsEditView } from "@console/shared";
import { readAutomationsBuilder } from "../services/automationsBuilderBridge.js";
import { readGroupBudget } from "../services/backtestBridge.js";
import { backtestResultsOn, getFeatures } from "../services/featuresBridge.js";
import { callConfigCli } from "../services/configBridge.js";

/**
 * A group's budget read from its members' pinned backtest runs, for the Groups page's suggestion beside
 * the allocation, max risk and shares. The members (those not retired) come from the automations module's
 * groups view, each one's pinned run from its Builder read; the reading is the backtest package's (`read
 * group-budget`), passed through: nothing is computed here. Until every member has a pinned run there is
 * nothing to read, and the members without one are named. A reading over a licensed run is withheld
 * while licensed results are not shown.
 */

const RUN = (name: string) => new RegExp(`^builder-${name}-\\d{10}$`);

export async function readGroupBudgetFor(group: string): Promise<GroupBudgetFromBacktest> {
  const empty: GroupBudgetFromBacktest = { group, pinned: {}, missing: [], withheld: false, error: null, reading: null };
  const viewRes = await callConfigCli({ op: "automation_groups" });
  if (!viewRes.ok) return { ...empty, error: viewRes.error ?? "the groups could not be read" };
  const view = viewRes as unknown as GroupsEditView;
  const g = (view.groups ?? []).find((x) => x.name === group);
  if (g === undefined) return { ...empty, error: `no group named ${group}` };
  const members = g.members.filter((m) => !m.retired).map((m) => m.name);
  const builder = await readAutomationsBuilder();
  if (builder.error !== null) return { ...empty, error: builder.error };
  const pinned: Record<string, string> = {};
  const missing: string[] = [];
  for (const name of members) {
    const run = builder.automations.find((a) => a.name === name)?.reference ?? null;
    if (run !== null && RUN(name).test(run)) pinned[name] = run;
    else missing.push(name);
  }
  if (members.length === 0 || missing.length > 0) return { ...empty, pinned, missing };
  const res = await readGroupBudget(Object.entries(pinned).map(([n, r]) => `${n}=${r}`));
  if (!res.ok || res.data === null) return { ...empty, pinned, error: res.error };
  const licensed = res.data["licensed"] !== false;
  if (licensed && !backtestResultsOn(await getFeatures())) return { ...empty, pinned, withheld: true };
  return { group, pinned, missing: [], withheld: false, error: null, reading: res.data as unknown as GroupBudgetFromBacktest["reading"] };
}
