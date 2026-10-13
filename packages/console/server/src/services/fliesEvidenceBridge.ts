import path from "node:path";
import type {
  BridgedResult,
  FliesGateSessions,
  FliesLiveGap,
  FliesSelectorSessions,
} from "@console/shared";
import type { ConsoleConfig } from "../config.js";
import { spawnModuleCli } from "./moduleCli.js";

/**
 * Three flies evidence reads this package must not compute for itself, asked of the module:
 *
 * - **`live-gap`** (`analytics.live_paper_gap`): the live arm's sessions, each paired with paper's
 *   same arm -- the net, fill, fee and completion gaps, and the matching that decides which paper
 *   entry is a live entry's twin. The matching rule and the `completion_rule` break it splits at
 *   (paper paying the live limit from 2026-10-05) are the module's.
 * - **`gate-sessions`** (`analytics.gate_sessions`): each gate arm over its OWN comparison window,
 *   taken from the ledger's `measurement_breaks` through `core.regimecuts.era_bounds` -- the
 *   writer's own era rule, not the console's `ERAS` table -- with whether the arm's one variable
 *   acted that session and its net against control's.
 * - **`selector-sessions`** (`analytics.selector_sessions`): the selector's choices, whether each
 *   session departed from control, and each frozen model read under procedure v2 (a cell's interval
 *   and whether it is clear of zero is `selector._clear`, never re-derived here).
 *
 * "Mirror a query, bridge a derivation" (CLAUDE.md): all three are derivations. Every verb opens
 * the ledgers read-only (`cli._open_ro`), so a poll can never migrate a module's store. Memoised
 * per verb: the numbers only move when a session settles, and the page polls.
 */

const UNAVAILABLE =
  "flies evidence unavailable — the flies package must be installed (pip install -e packages/flies)";

export type FliesEvidenceVerb = "live-gap" | "gate-sessions" | "selector-sessions";

type Spawned = ReturnType<typeof spawnModuleCli>;

function argv(config: ConsoleConfig, verb: FliesEvidenceVerb): string[] {
  const paper = path.join(config.paths.fliesDir, "paper_trades.db");
  const head = ["-m", "cherrypick.flies.cli", "--db", paper, verb];
  if (verb === "live-gap") return [...head, "--live-db", path.join(config.paths.fliesDir, "live_trades.db")];
  return head;
}

function spawnVerb(config: ConsoleConfig, verb: FliesEvidenceVerb): Spawned {
  return spawnModuleCli(argv(config, verb), UNAVAILABLE);
}

let caller = spawnVerb;

/** Swap the subprocess out in tests. Pass nothing to restore the real one. */
export function setFliesEvidenceCaller(fn?: typeof spawnVerb): void {
  caller = fn ?? spawnVerb;
}

const TTL_MS = 120_000;
const cache = new Map<string, { at: number; value: Spawned }>();

/** Drop the memoised responses. Tests only. */
export function resetFliesEvidenceCache(): void {
  cache.clear();
}

function read<T>(config: ConsoleConfig, verb: FliesEvidenceVerb, now: number): BridgedResult<T> {
  const key = `${config.paths.fliesDir}|${verb}`;
  let hit = cache.get(key);
  if (hit === undefined || now - hit.at >= TTL_MS) {
    hit = { at: now, value: caller(config, verb) };
    cache.set(key, hit);
  }
  const res = hit.value;
  if (!res.ok || res.json === null) return { ok: false, data: null, error: res.error ?? UNAVAILABLE };
  if (res.json["ok"] !== true) {
    const err = res.json["error"];
    return { ok: false, data: null, error: typeof err === "string" ? err : `${UNAVAILABLE} — refused` };
  }
  return { ok: true, data: res.json as unknown as T, error: null };
}

export function readFliesLiveGap(config: ConsoleConfig, now = Date.now()): BridgedResult<FliesLiveGap> {
  return read<FliesLiveGap>(config, "live-gap", now);
}

export function readFliesGateSessions(config: ConsoleConfig, now = Date.now()): BridgedResult<FliesGateSessions> {
  return read<FliesGateSessions>(config, "gate-sessions", now);
}

export function readFliesSelector(config: ConsoleConfig, now = Date.now()): BridgedResult<FliesSelectorSessions> {
  return read<FliesSelectorSessions>(config, "selector-sessions", now);
}
