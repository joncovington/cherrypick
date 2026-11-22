import type { AutomationReadingRow, AutomationsReadings } from "@console/shared";
import { readAutomationsBuilder } from "../services/automationsBuilderBridge.js";
import { readAutomationReadings } from "../services/backtestBridge.js";
import { backtestResultsOn, getFeatures } from "../services/featuresBridge.js";

/**
 * Every automation with its paper record against its pinned Builder run, for the automations'
 * vs-backtest tab and the reading chip on Groups' rows. The pins and the declared samples (each
 * forward rule's `min_obs`) are the automations module's Builder read; the readings are the backtest
 * package's, all in one read (`read automation-readings`), passed through. A licensed run's figures are
 * withheld while licensed results are not shown, as on the automation's own page.
 */

const RUN = (name: string) => new RegExp(`^builder-${name}-\\d{10}$`);

const obj = (v: unknown): Record<string, unknown> | null => (v !== null && typeof v === "object" && !Array.isArray(v) ? (v as Record<string, unknown>) : null);

export async function readAllAutomationReadings(): Promise<AutomationsReadings> {
  const builder = await readAutomationsBuilder();
  if (builder.error !== null) return { automations: [], error: builder.error };
  const rows: AutomationReadingRow[] = builder.automations.map((a) => ({
    name: a.name,
    group: a.group,
    pinned: a.reference,
    withheld: false,
    error: null,
    state: null,
    paper: null,
    sample: null,
    reference: null,
  }));
  const pairs: string[] = [];
  for (const [i, a] of builder.automations.entries()) {
    if (a.reference === null) continue;
    if (!RUN(a.name).test(a.reference)) {
      rows[i] = { ...rows[i]!, error: "the pinned run's name is not this automation's" };
      continue;
    }
    const minObs = a.forward?.["min_obs"];
    const declared = typeof minObs === "number" && Number.isInteger(minObs) && minObs > 0 ? `:${String(minObs)}` : "";
    pairs.push(`${a.name}=${a.reference}${declared}`);
  }
  if (pairs.length === 0) return { automations: rows, error: null };
  const res = await readAutomationReadings(pairs);
  if (!res.ok || res.data === null) return { automations: rows, error: res.error };
  const readings = obj(res.data["readings"]) ?? {};
  const showLicensed = backtestResultsOn(await getFeatures());
  return {
    error: null,
    automations: rows.map((row) => {
      const r = obj(readings[row.name]);
      if (row.pinned === null || r === null) return row;
      if (r["ok"] !== true) return { ...row, error: String(r["error"] ?? "the reading failed") };
      if (r["licensed"] !== false && !showLicensed) return { ...row, withheld: true };
      return {
        ...row,
        state: r["state"] as AutomationReadingRow["state"],
        paper: r["paper"] as AutomationReadingRow["paper"],
        sample: r["sample"] as AutomationReadingRow["sample"],
        reference: r["reference"] as AutomationReadingRow["reference"],
      };
    }),
  };
}
