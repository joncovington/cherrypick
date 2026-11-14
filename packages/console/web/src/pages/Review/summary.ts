import type { ReviewArm, ReviewModule } from "@console/shared";

/**
 * The suite review's layout helpers: what needs attention, which modules get a row in the summary
 * table and which share the "no trades" line, and how many arms a row shows before "n more". Every
 * figure stays the fact set's; these only choose what goes where.
 */

export interface Flag {
  level: "warn" | "err";
  /** The module the flag is about, so the page can link it to that module's row. */
  module: string | null;
  /** The sentence after the module's name. */
  text: string;
}

function money(v: number | null): string {
  return v === null ? "—" : v.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

/** What a reader needs before anything else. An empty list is a real and good answer. */
export function attention(modules: ReviewModule[], status: string | null): Flag[] {
  const out: Flag[] = [];
  for (const m of modules) {
    if (!m.ok) {
      out.push({ level: "err", module: m.module, text: `could not be read — ${m.reason ?? "no reason given"}` });
      continue;
    }
    if (m.loopTicked === false) out.push({ level: "err", module: m.module, text: "did not tick at all — a stopped loop, not a quiet day" });
    if (m.errors) out.push({ level: "err", module: m.module, text: `logged ${String(m.errors)} iteration error(s)` });
    if (m.suspectedBreak) {
      out.push({
        level: "warn",
        module: m.module,
        text:
          `looks like a regime change with no journaled break — ${String(m.suspectedBreak.trades)} trades ` +
          `against a trailing median of ${String(Math.round(m.suspectedBreak.trailingMedian))} (${String(m.suspectedBreak.ratio)}×)`,
      });
    }
    if (m.breaks === null) {
      out.push({ level: "warn", module: m.module, text: "does not track measurement breaks — its trend assumes a continuity nothing verified" });
    }
  }
  if (status === "provisional") {
    const carried = modules.filter((m) => m.ok && m.carriedPositions > 0);
    if (carried.length === 0) {
      out.push({ level: "warn", module: null, text: "Provisional — the overnight module has not settled, so its realised P&L lands in the next session." });
    }
    for (const m of carried) {
      const unknown = m.carriedCapitalUnknown > 0 ? ` (+${String(m.carriedCapitalUnknown)} of unknown capital)` : "";
      out.push({
        level: "warn",
        module: m.module,
        text: `carried ${String(m.carriedPositions)} position(s) overnight, ${money(m.carriedCapital)} at risk${unknown}; provisional — its realised P&L lands next session`,
      });
    }
  }
  return out;
}

export interface IdleModule {
  module: string;
  /** Why it is idle, when that is itself a finding (a loop that never ticked). */
  note: string | null;
}

/**
 * The table's rows are the modules with something to show: a trade closed, a position carried, or a
 * store that could not be read. Every other module shares one "no trades" line.
 */
export function splitModules(modules: ReviewModule[]): { rows: ReviewModule[]; idle: IdleModule[] } {
  const rows: ReviewModule[] = [];
  const idle: IdleModule[] = [];
  for (const m of modules) {
    if (!m.ok || m.closed > 0 || m.carriedPositions > 0) rows.push(m);
    else idle.push({ module: m.module, note: m.loopTicked === false ? "loop did not tick" : null });
  }
  return { rows, idle };
}

export const ARMS_TOP = 3;

/** An open row's arms, largest net first: the top three, or all of them once asked. */
export function shownArms(arms: ReviewArm[], all: boolean): { shown: ReviewArm[]; more: number } {
  const sorted = [...arms].sort((a, b) => b.net - a.net);
  if (all || sorted.length <= ARMS_TOP) return { shown: sorted, more: 0 };
  return { shown: sorted.slice(0, ARMS_TOP), more: sorted.length - ARMS_TOP };
}

/** Arms that used one centring rule all session are that rule's arm, not independent observations. */
export function collapsedArms(m: ReviewModule): string[] {
  const byRule = new Map<string, string[]>();
  for (const a of m.arms) {
    if (!a.centredBy) continue;
    byRule.set(a.centredBy, [...(byRule.get(a.centredBy) ?? []), a.arm]);
  }
  return [...byRule.entries()].filter(([, arms]) => arms.length > 1).map(([rule, arms]) => `${arms.slice().sort().join(", ")} all centred ${rule}`);
}

/** The row's anchor id, so an attention line can jump to it. */
export function moduleAnchor(module: string): string {
  return `review-mod-${module.replace(/[^a-z0-9]+/gi, "-").toLowerCase()}`;
}
