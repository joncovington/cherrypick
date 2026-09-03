import path from "node:path";
import type { ConsoleConfig } from "../config.js";
import { withReadOnlyDb, hasTable, str } from "./db.js";
import type { ModulePerformanceGroup, PerformanceModuleId } from "./performance.js";

/**
 * The `advised:<base>` twin beside each control -- paired by session, with the experiment id and
 * underpowered verdict that PRODUCED the twin, read from `advisor.db` rather than recomputed here.
 * `packages/advisor`'s own rule: verdicts are computed there (ledger readers -> compare_profiles ->
 * qualify_readings) and stored on the experiment row; a second computation here would be a second
 * opinion free to drift, the same mistake `services/report.ts` already made once for net rules.
 *
 * `bounds.advised_tag(module, base_profile, strategy)` is `f"advised:{base_profile}"` (or
 * `f"advised:{base_profile}:{strategy}"` for earnings) -- so a performance group's tag is paired to
 * its base by stripping the `advised:` prefix, and the experiment lookup joins on `base_profile`
 * alone (before any `:strategy` suffix), matching `verdicts.py`'s own `base_profile`/`strategy`
 * split.
 */

export interface AdvisedPair {
  advised: string;
  base: string;
  /** Sessions BOTH books actually recorded a net for, in this read's window -- not the advised
   * book's trade count and not the experiment's `sessions_run` (which counts a loop APPLYING the
   * artifact, not a session with paired data to compare). */
  sessionsPaired: number;
  experimentId: string | null;
  /** `null` when no experiment row was found to ask (a pair can exist without a live experiment --
   * config-authored `advised:` books are not unheard of); `true`/`false` is the stored verdict's
   * own answer once one has been computed. */
  underpowered: boolean | null;
}

const ADVISED_PREFIX = "advised:";

function dbPath(config: ConsoleConfig): string {
  return path.join(config.paths.advisorDir, "advisor.db");
}

/** How many session dates two [session, net] series share. */
function sharedSessionCount(a: Array<[string, number]>, b: Array<[string, number]>): number {
  const bSessions = new Set(b.map(([session]) => session));
  let n = 0;
  for (const [session] of a) if (bSessions.has(session)) n++;
  return n;
}

/** Look up the most recent experiment for (module, baseProfile) -- `{id, underpowered}` from
 * whatever the row's own `verdict_json` says, or both null when no row exists (or the db/table
 * isn't there yet: a real, ordinary state, not an error). */
function lookupExperiment(
  config: ConsoleConfig,
  module: PerformanceModuleId,
  baseProfile: string,
): { experimentId: string | null; underpowered: boolean | null } {
  const empty = { experimentId: null, underpowered: null };
  return withReadOnlyDb(dbPath(config), empty, (db) => {
    if (!hasTable(db, "experiments")) return empty;
    const row = db
      .prepare<[string, string], Record<string, unknown>>(
        "SELECT id, verdict_json FROM experiments WHERE module = ? AND base_profile = ?" +
          " ORDER BY created_at DESC LIMIT 1",
      )
      .get(module, baseProfile);
    if (row === undefined) return empty;
    const experimentId = str(row["id"]);
    let underpowered: boolean | null = null;
    const rawVerdict = row["verdict_json"];
    if (typeof rawVerdict === "string" && rawVerdict !== "") {
      try {
        const parsed = JSON.parse(rawVerdict) as { underpowered?: unknown };
        underpowered = parsed.underpowered === true ? true : parsed.underpowered === false ? false : null;
      } catch {
        underpowered = null;
      }
    }
    return { experimentId, underpowered };
  });
}

export function readAdvisedPairs(
  config: ConsoleConfig,
  module: PerformanceModuleId,
  groups: ModulePerformanceGroup[],
): AdvisedPair[] {
  const byTag = new Map(groups.map((g) => [g.tag, g]));
  const advisedGroups = groups.filter((g) => g.tag.startsWith(ADVISED_PREFIX));

  return advisedGroups.map((advisedGroup) => {
    const base = advisedGroup.tag.slice(ADVISED_PREFIX.length);
    const baseGroup = byTag.get(base);
    const sessionsPaired =
      baseGroup === undefined ? 0 : sharedSessionCount(advisedGroup.sessionNets, baseGroup.sessionNets);
    // base_profile never carries earnings' :strategy suffix (verdicts.py keeps that as a separate
    // parameter), so the experiment lookup joins on the portion before any colon.
    const baseProfile = base.split(":")[0] ?? base;
    const { experimentId, underpowered } = lookupExperiment(config, module, baseProfile);
    return { advised: advisedGroup.tag, base, sessionsPaired, experimentId, underpowered };
  });
}
