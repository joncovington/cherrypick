import type { AdvisedPair, ModulePerformanceGroup, PerformanceModuleId } from "@console/shared";
import type { ConsoleConfig } from "../config.js";
import { declaredAdviceBase } from "./adviceDecl.js";
import { ADVISED_PREFIX, readExperimentIndex, resolveAdvisedTag } from "./experimentIndex.js";

/**
 * Each advised book beside the base it shadows -- paired by session, with the experiment that
 * PRODUCED the book and its stored underpowered verdict, read from `advisor.db` rather than
 * recomputed here (`packages/advisor`'s own rule: verdicts are computed there, through ledger
 * readers -> compare_profiles -> qualify_readings, and stored on the experiment row; a second
 * computation here would be a second opinion free to drift, the mistake `services/report.ts`
 * already made once for net rules).
 *
 * One pair PER EXPERIMENT. Since 2026-09-17 every experiment writes its own book,
 * `advised:<experiment name>`, so two experiments on one base are two pairs against the same
 * control -- the tag no longer says which base, the advisor's row does. The pairing goes through
 * `experimentIndex.ts` in its one order: the stamp `core.metrics` groups by
 * (`<tag>@<experiment id>`), then the tag against the experiment's own, then the legacy reading
 * of `advised:<base>` for rows written before the change that no experiment claims. Those pair
 * once against the base the tag names (or, when that book is not in this window, the module's
 * declared base) and are flagged `unstamped`: the Advisor page's stored verdicts, windowed by
 * the advisor itself, are the per-experiment read for that history. Deliberately no date
 * inference here -- a second attribution rule free to drift from the advisor's own.
 */

/** How many session dates two [session, net] series share. */
function sharedSessionCount(a: Array<[string, number]>, b: Array<[string, number]>): number {
  const bSessions = new Set(b.map(([session]) => session));
  let n = 0;
  for (const [session] of a) if (bSessions.has(session)) n++;
  return n;
}

export function readAdvisedPairs(
  config: ConsoleConfig,
  module: PerformanceModuleId,
  groups: ModulePerformanceGroup[],
): AdvisedPair[] {
  const byTag = new Map(groups.map((g) => [g.tag, g]));
  const advisedGroups = groups.filter((g) => g.tag.startsWith(ADVISED_PREFIX));
  if (advisedGroups.length === 0) return [];

  const index = readExperimentIndex(config, module);
  let declared: string | null | undefined;

  return advisedGroups.map((advisedGroup) => {
    const resolved = resolveAdvisedTag(advisedGroup.tag, index);
    let base = resolved.base;
    if (resolved.experiment === null && !byTag.has(base)) {
      // A legacy tag whose named base has no rows in this window: fall back to the base the
      // module declares, keeping the strategy suffix earnings carries. Read once per call.
      if (declared === undefined) declared = declaredAdviceBase(config, module);
      if (declared !== null) base = resolved.strategy === null ? declared : `${declared}:${resolved.strategy}`;
    }
    const baseGroup = byTag.get(base);
    const sessionsPaired =
      baseGroup === undefined ? 0 : sharedSessionCount(advisedGroup.sessionNets, baseGroup.sessionNets);
    const experiment = resolved.experiment;
    return {
      advised: advisedGroup.tag,
      base,
      // The stamp is the attribution even when advisor.db has no row for it (a store rebuilt, an
      // experiment pruned): the ledger said which experiment wrote these rows.
      experimentId: experiment?.id ?? resolved.stampedId,
      experimentName: experiment?.name ?? null,
      attribution: experiment !== null ? resolved.attribution : resolved.stampedId !== null ? "stamp" : "none",
      unstamped: experiment === null && resolved.stampedId === null,
      sessionsPaired,
      underpowered: experiment?.underpowered ?? null,
    };
  });
}
