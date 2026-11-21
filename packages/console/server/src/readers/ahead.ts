import type { AheadModule, SessionsAhead } from "@console/shared";
import { AsyncMemo } from "../services/asyncMemo.js";
import { spawnModuleCliAsync, type SpawnedJson } from "../services/moduleCli.js";

/**
 * One arm's next sessions (`core.ahead`, through the module's own command: `python -m
 * cherrypick.meic.cli ahead --arm A`, `python -m cherrypick.automations.cli ahead --name A`): what each
 * of its dated gates does there, the gates that read a value at the time, and its record on each kind
 * of day. Computed in Python from the calendar and the module's own record; passed through here.
 */

const UNAVAILABLE = "the sessions ahead are unavailable — the module's package must be installed";
// The calendar moves once a day and the record when a session settles.
const TTL_MS = 300_000;

const ARGV: Record<AheadModule, (arm: string) => string[]> = {
  meic: (arm) => ["-m", "cherrypick.meic.cli", "ahead", "--arm", arm],
  automations: (arm) => ["-m", "cherrypick.automations.cli", "ahead", "--name", arm],
};

type Caller = (module: AheadModule, arm: string) => SpawnedJson | Promise<SpawnedJson>;
const spawnCli: Caller = (module, arm) => spawnModuleCliAsync(ARGV[module](arm), UNAVAILABLE, [0, 1], 60_000);
let caller: Caller = spawnCli;
const memo = new AsyncMemo<SpawnedJson>();

/** Swap the subprocess out in tests. Pass nothing to restore the real one. */
export function setAheadCaller(fn?: Caller): void {
  caller = fn ?? spawnCli;
  memo.clear();
}

const list = <T>(v: unknown): T[] => (Array.isArray(v) ? (v as T[]) : []);

export async function readSessionsAhead(module: AheadModule, arm: string, now: number = Date.now()): Promise<SessionsAhead> {
  const empty: SessionsAhead = {
    module,
    arm,
    era_start: null,
    start: null,
    min_sessions: null,
    sessions: [],
    gates: [],
    value_gates: [],
    records: null,
    error: null,
  };
  const res = await memo.get(`${module}|${arm}`, TTL_MS, now, () => caller(module, arm));
  if (!res.ok || res.json === null) return { ...empty, error: res.ok ? "the sessions ahead gave nothing" : res.error };
  const j = res.json;
  if (j["ok"] !== true) return { ...empty, error: String(j["error"] ?? "the sessions ahead failed") };
  return {
    ...empty,
    era_start: typeof j["era_start"] === "string" ? j["era_start"] : null,
    start: typeof j["start"] === "string" ? j["start"] : null,
    min_sessions: typeof j["min_sessions"] === "number" ? j["min_sessions"] : null,
    sessions: list(j["sessions"]),
    gates: list(j["gates"]),
    value_gates: list(j["value_gates"]),
    records: Array.isArray(j["records"]) ? (j["records"] as SessionsAhead["records"]) : null,
  };
}
