import type { AutomationEntries, AutomationsEntries, AutomationsEntriesRow, EntryFunnel } from "@console/shared";
import { AsyncMemo } from "../services/asyncMemo.js";
import { spawnModuleCliAsync, type SpawnedJson } from "../services/moduleCli.js";
import { readAutomationReadingFor } from "./automationReading.js";

/**
 * How often each automation enters and where the sessions it did not enter stopped
 * (`python -m cherrypick.automations.cli entries`): the session register cut into the steps of the
 * engine's decision, by the automations package. For one automation, its pinned run's reasons (from
 * the backtest reference its reading carries, withheld as the reading is) go to the same command, so
 * the run's funnel is cut by the same steps. Nothing is computed here.
 */

const UNAVAILABLE =
  "the entries read is unavailable — the automations package must be installed (pip install -e packages/automations)";
const TTL_MS = 60_000;

type Caller = (argv: string[]) => SpawnedJson | Promise<SpawnedJson>;
const spawnCli: Caller = (argv) => spawnModuleCliAsync(["-m", "cherrypick.automations.cli", "entries", ...argv], UNAVAILABLE);
let caller: Caller = spawnCli;
const memo = new AsyncMemo<SpawnedJson>();

/** Swap the subprocess out in tests. Pass nothing to restore the real one. */
export function setAutomationEntriesCaller(fn?: Caller): void {
  caller = fn ?? spawnCli;
  memo.clear();
}

function failed(res: SpawnedJson): string | null {
  if (!res.ok || res.json === null) return res.ok ? "the entries read gave nothing" : res.error;
  if (res.json["ok"] !== true) return String(res.json["error"] ?? "the entries read failed");
  return null;
}

export async function readAutomationsEntries(now: number = Date.now()): Promise<AutomationsEntries> {
  const res = await memo.get("all", TTL_MS, now, () => caller([]));
  const error = failed(res);
  if (error !== null) return { automations: [], error };
  const rows = Array.isArray(res.json!["automations"]) ? (res.json!["automations"] as AutomationsEntriesRow[]) : [];
  return { automations: rows, error: null };
}

export async function readAutomationEntriesFor(name: string, now: number = Date.now()): Promise<AutomationEntries> {
  const empty: AutomationEntries = {
    name,
    pinned: null,
    withheld: false,
    error: null,
    reference: null,
    first: null,
    last: null,
    paper: null,
    run: null,
    causes: [],
    days: [],
  };
  // The reading carries the pin, the gate and the run's reasons; its own error (no pin, a run gone)
  // leaves paper's funnel to stand alone.
  const reading = await readAutomationReadingFor(name);
  const ref = reading.reading?.reference ?? null;
  const reasons = ref?.reasons && Object.keys(ref.reasons).length > 0 ? ref.reasons : null;
  const argv = ["--name", name, ...(reasons ? ["--run-reasons", JSON.stringify(reasons)] : [])];
  const res = await memo.get(argv.join(" "), TTL_MS, now, () => caller(argv));
  const error = failed(res);
  const head = {
    ...empty,
    pinned: reading.pinned,
    withheld: reading.withheld,
    reference:
      ref && reasons
        ? { run: ref.run, licensed: reading.reading!.licensed, first: ref.first, last: ref.last, sessions: ref.sessions }
        : null,
  };
  if (error !== null) return { ...head, error };
  const j = res.json!;
  return {
    ...head,
    first: (j["first"] as string | null) ?? null,
    last: (j["last"] as string | null) ?? null,
    paper: (j["paper"] as EntryFunnel | null) ?? null,
    run: reasons ? ((j["run"] as EntryFunnel | null) ?? null) : null,
    causes: Array.isArray(j["causes"]) ? (j["causes"] as AutomationEntries["causes"]) : [],
    days: Array.isArray(j["days"]) ? (j["days"] as AutomationEntries["days"]) : [],
  };
}
