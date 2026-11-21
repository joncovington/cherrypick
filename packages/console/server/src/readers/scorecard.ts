import type { Scorecard, ScorecardRow } from "@console/shared";
import { AsyncMemo } from "../services/asyncMemo.js";
import { spawnModuleCliAsync, type SpawnedJson } from "../services/moduleCli.js";

/**
 * Compare's scorecard (`python -m cherrypick.cli scorecard`): one row per arm and automation in the
 * suite, with its research verdict, its vs-backtest and vs-control readings, its sample, net and
 * drawdown, and the reason it needs a look. The orchestrator joins what the suite already decided
 * (`orchestrator/scorecard.py`), and withholds a forward state set against a licensed reference while
 * licensed results are hidden; this passes the reply through and decides nothing.
 */

const UNAVAILABLE = "the scorecard is unavailable — the orchestrator must be installed";
// Every module's comparison is read; it moves when a session settles.
const TTL_MS = 300_000;
const ARGV = ["-m", "cherrypick.cli", "scorecard"];

type Caller = () => SpawnedJson | Promise<SpawnedJson>;
const spawnCli: Caller = () => spawnModuleCliAsync(ARGV, UNAVAILABLE, [0], 120_000);
let caller: Caller = spawnCli;
const memo = new AsyncMemo<SpawnedJson>();

/** Swap the subprocess out in tests. Pass nothing to restore the real one. */
export function setScorecardCaller(fn?: Caller): void {
  caller = fn ?? spawnCli;
  memo.clear();
}

const list = <T>(v: unknown): T[] => (Array.isArray(v) ? (v as T[]) : []);
const rec = (v: unknown): Record<string, unknown> => (v && typeof v === "object" && !Array.isArray(v) ? (v as Record<string, unknown>) : {});

export async function readScorecard(now: number = Date.now()): Promise<Scorecard> {
  const empty: Scorecard = { rows: [], eras: {}, looks: [], counts: {}, min_sessions: null, modules: [], error: null };
  const res = await memo.get("scorecard", TTL_MS, now, () => caller());
  if (!res.ok || res.json === null) return { ...empty, error: res.ok ? "the scorecard gave nothing" : res.error };
  const j = res.json;
  if (j["ok"] !== true) return { ...empty, error: String(j["error"] ?? "the scorecard failed") };
  return {
    rows: list<ScorecardRow>(j["rows"]),
    eras: rec(j["eras"]) as Scorecard["eras"],
    looks: list(j["looks"]),
    counts: rec(j["counts"]) as Scorecard["counts"],
    min_sessions: typeof j["min_sessions"] === "number" ? j["min_sessions"] : null,
    modules: list<string>(j["modules"]),
    error: null,
  };
}
