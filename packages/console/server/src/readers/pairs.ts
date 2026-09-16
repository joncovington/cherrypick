import path from "node:path";
import type { AdvisedPair, ModulePerformanceGroup, PerformanceModuleId } from "@console/shared";
import type { ConsoleConfig } from "../config.js";
import { withReadOnlyDb, hasTable, str } from "./db.js";

/**
 * The `advised:<base>` twin beside each control -- paired by session, with the experiment id and
 * underpowered verdict that PRODUCED the twin, read from `advisor.db` rather than recomputed here.
 *
 * One pair PER EXPERIMENT (2026-09-16). The tag names a book, and every experiment on that base
 * reuses it in turn, so a pair per tag pooled three experiments' rows under the id of whichever
 * was most recent. `core.metrics` now groups an advised row stamped with its experiment under
 * `advised:<base>@<experiment id>`; each such group is its own pair, looked up by that id. Rows
 * written before the stamp existed stay under the bare tag and pair once, flagged `unstamped`,
 * with no experiment attributed -- the Advisor page's stored verdicts (windowed by the advisor
 * itself) are the per-experiment read for that history; inferring it here from dates would be a
 * second attribution rule free to drift from the first.
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

/** The stored `underpowered` verdict for one experiment id -- from whatever the row's own
 * `verdict_json` says, or null when no row exists (or the db/table isn't there yet: a real,
 * ordinary state, not an error). */
function lookupExperiment(
  config: ConsoleConfig,
  experimentId: string,
): { experimentId: string | null; underpowered: boolean | null } {
  const empty = { experimentId: null, underpowered: null };
  return withReadOnlyDb(dbPath(config), empty, (db) => {
    if (!hasTable(db, "experiments")) return empty;
    const row = db
      .prepare<[string], Record<string, unknown>>("SELECT id, verdict_json FROM experiments WHERE id = ?")
      .get(experimentId);
    if (row === undefined) return empty;
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

/** `advised:<base>@<experiment>` -> {base, experimentId}; the bare tag -> {base, null}. */
export function splitAdvisedTag(tag: string): { base: string; experimentId: string | null } {
  const rest = tag.slice(ADVISED_PREFIX.length);
  const at = rest.indexOf("@");
  if (at === -1) return { base: rest, experimentId: null };
  return { base: rest.slice(0, at), experimentId: rest.slice(at + 1) || null };
}

export function readAdvisedPairs(
  config: ConsoleConfig,
  _module: PerformanceModuleId,
  groups: ModulePerformanceGroup[],
): AdvisedPair[] {
  const byTag = new Map(groups.map((g) => [g.tag, g]));
  const advisedGroups = groups.filter((g) => g.tag.startsWith(ADVISED_PREFIX));

  return advisedGroups.map((advisedGroup) => {
    const { base, experimentId: stamped } = splitAdvisedTag(advisedGroup.tag);
    const baseGroup = byTag.get(base);
    const sessionsPaired =
      baseGroup === undefined ? 0 : sharedSessionCount(advisedGroup.sessionNets, baseGroup.sessionNets);
    if (stamped === null) {
      return { advised: advisedGroup.tag, base, unstamped: true, sessionsPaired, experimentId: null, underpowered: null };
    }
    const { experimentId, underpowered } = lookupExperiment(config, stamped);
    return {
      advised: advisedGroup.tag,
      base,
      unstamped: false,
      sessionsPaired,
      // The stamp is the attribution even when advisor.db has no row for it (a store rebuilt,
      // an experiment pruned): the ledger said which experiment wrote these rows.
      experimentId: experimentId ?? stamped,
      underpowered,
    };
  });
}
