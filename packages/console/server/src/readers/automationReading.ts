import type { AutomationReading } from "@console/shared";
import { readAutomationsBuilder } from "../services/automationsBuilderBridge.js";
import { readAutomationReading } from "../services/backtestBridge.js";
import { backtestResultsOn, getFeatures } from "../services/featuresBridge.js";

/**
 * One automation's paper record set against its pinned Builder run, for its page's "Is it working?".
 * The pin and the declared sample (the forward rule's `min_obs`) come from the automations module's
 * Builder read; the reading itself is the backtest package's (`read automation-reading`), passed
 * through: nothing is computed here. A licensed run's figures are withheld while licensed results are
 * not shown, as the Builder's run list withholds them.
 */

const RUN = (name: string) => new RegExp(`^builder-${name}-\\d{10}$`);

export async function readAutomationReadingFor(name: string): Promise<AutomationReading> {
  const empty = { name, pinned: null, withheld: false, error: null, reading: null };
  const builder = await readAutomationsBuilder();
  if (builder.error !== null) return { ...empty, error: builder.error };
  const row = builder.automations.find((a) => a.name === name);
  if (row === undefined) return { ...empty, error: `no automation named ${name}` };
  const pinned = row.reference;
  if (pinned === null) return empty;
  if (!RUN(name).test(pinned)) return { ...empty, pinned, error: "the pinned run's name is not this automation's" };
  const minObs = row.forward?.["min_obs"];
  const declared = typeof minObs === "number" && Number.isInteger(minObs) && minObs > 0 ? minObs : null;
  const res = await readAutomationReading(name, pinned, declared);
  if (!res.ok || res.data === null) return { ...empty, pinned, error: res.error };
  const licensed = res.data["licensed"] !== false;
  if (licensed && !backtestResultsOn(await getFeatures())) return { ...empty, pinned, withheld: true };
  const { reference, state, paper, sample } = res.data as Record<string, unknown>;
  return {
    name,
    pinned,
    withheld: false,
    error: null,
    reading: { state, licensed, reference, paper, sample } as AutomationReading["reading"],
  };
}
