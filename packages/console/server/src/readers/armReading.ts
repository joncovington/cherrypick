import type { ArmBacktestReading, ArmRunMatch } from "@console/shared";
import { AsyncMemo } from "../services/asyncMemo.js";
import { backtestResultsOn, getFeatures } from "../services/featuresBridge.js";
import { spawnModuleCliAsync, type SpawnedJson } from "../services/moduleCli.js";

/**
 * A module arm against its backtest (`python -m cherrypick.backtest read arm-reading`): the stored run
 * whose arm has the paper arm's settings and the paper record per session against it, computed by the
 * backtest package and passed through. Every module run replays the vendor's store, so the figures are
 * withheld while licensed results are hidden (fail closed: a reply not plainly unlicensed is licensed).
 */

const UNAVAILABLE = "the arm's backtest read is unavailable — the backtest package must be installed";
const TTL_MS = 120_000;

type Caller = (module: string, arm: string) => SpawnedJson | Promise<SpawnedJson>;
const spawnCli: Caller = (module, arm) =>
  spawnModuleCliAsync(["-m", "cherrypick.backtest", "read", "arm-reading", "--module", module, "--name", arm], UNAVAILABLE, [0, 1], 90_000);
let caller: Caller = spawnCli;
const memo = new AsyncMemo<SpawnedJson>();

/** Swap the subprocess out in tests. Pass nothing to restore the real one. */
export function setArmReadingCaller(fn?: Caller): void {
  caller = fn ?? spawnCli;
  memo.clear();
}

export async function readArmReading(module: string, arm: string, now: number = Date.now()): Promise<ArmBacktestReading> {
  const empty: ArmBacktestReading = { module, arm, unit: "session", matches: [], chosen: null, paper_from: null, withheld: false, error: null, reading: null };
  const res = await memo.get(`${module}|${arm}`, TTL_MS, now, () => caller(module, arm));
  if (!res.ok || res.json === null) return { ...empty, error: res.ok ? "the arm's backtest read gave nothing" : res.error };
  const j = res.json;
  if (j["ok"] !== true) return { ...empty, error: String(j["error"] ?? "the arm's backtest read failed") };
  const matches = Array.isArray(j["matches"]) ? (j["matches"] as ArmRunMatch[]) : [];
  const chosen = (j["chosen"] ?? null) as ArmRunMatch | null;
  const licensed = j["licensed"] !== false;
  if (licensed && chosen !== null && !backtestResultsOn(await getFeatures())) {
    // Which runs match is named; what they say is not.
    return { ...empty, matches, chosen, withheld: true };
  }
  return {
    ...empty,
    matches,
    chosen,
    paper_from: typeof j["paper_from"] === "string" ? j["paper_from"] : null,
    reading: (j["reading"] ?? null) as ArmBacktestReading["reading"],
  };
}
