import type { SuiteFeatures } from "@console/shared";
import { isModuleVisible } from "../../lib/visibility";

/**
 * The suite's page list, in one place.
 *
 * Three things read it and must not disagree: the left rail (the ONE nav, mounted in the shell
 * on every page since 2026-10-11 — the header's hamburger menu is gone), the rail's link
 * titles (which carry the shortcut hints), and the keyboard handler that makes those hints do
 * something. A second nav reading a second copy is how the two end up listing
 * different modules.
 *
 * `key` is the shortcut character the rail advertises. It is advertised in exactly one place
 * (this file) and implemented in exactly one place (`lib/keyboardNav.ts`), so a hint cannot
 * survive the shortcut being dropped.
 */
export interface NavLinkDef {
  to: string;
  label: string;
  /** Exact-match route, for a path that would otherwise prefix others. */
  end?: boolean;
  key?: string;
  /** Where a suite page sits on the rail: above the modules (`live`, then `market`) or below them
   *  (`research`, then `suite`, Config last). Module links carry none. */
  section?: NavSection;
}

export type NavSection = "live" | "market" | "research" | "suite";

/**
 * The suite pages, in rail order (2026-10-18): real money first, then the market reads in the order
 * a day uses them (the morning report before the open, the market, then candidates), the modules
 * between, then the research surfaces and the suite's own pages at the bottom. GEX joined the market
 * reads from the module list: it records and reads the market, it trades nothing.
 */
export const SUITE_LINKS: readonly NavLinkDef[] = [
  { to: "/live", label: "Live", key: "l", section: "live" },
  { to: "/reports", label: "Reports", key: "r", section: "market" },
  { to: "/charts", label: "Charts", key: "c", section: "market" },
  { to: "/flow", label: "Options flow", key: "f", section: "market" },
  { to: "/gex", label: "GEX", key: "g", section: "market" },
  { to: "/screeners", label: "Screeners", key: "i", section: "market" },
  { to: "/compare", label: "Compare", key: "m", section: "research" },
  { to: "/backtest", label: "Backtest", key: "b", section: "research" },
  { to: "/advisor", label: "Advisor", key: "a", section: "research" },
  { to: "/system", label: "System", key: "s", section: "suite" },
];

/** The suite links above the modules, and those below them. */
export function railHalves<T extends NavLinkDef>(links: readonly T[]): { above: T[]; below: T[] } {
  const above = links.filter((l) => l.section === "live" || l.section === "market");
  return { above, below: links.filter((l) => !above.includes(l)) };
}

/**
 * The rail's Modules section (2026-10-30, docs/console-review-plan.md batch 4): the live-capable
 * modules, then the Experiments page. Each keeps its digit whether or not another is on: `2` is BWB
 * every day, so a module switched off no longer moves the others' keys.
 */
export const MODULE_LINKS: readonly NavLinkDef[] = [
  { to: "/meic", label: "MEIC", key: "1" },
  { to: "/bwb", label: "BWB", key: "2" },
  { to: "/earnings", label: "Earnings", key: "3" },
  { to: "/experiments", label: "Experiments", key: "4" },
];

/**
 * The modules the Experiments page holds: the paper-only experiments and the retired flies. Off the
 * rail; the rail opens one out under Experiments while you are on it. With MODULE_LINKS, every trading
 * module has a link (`test/navLinks.test.ts`): contango shipped 2026-10-05 with a route and no way to
 * reach it.
 */
export const EXPERIMENT_LINKS: readonly NavLinkDef[] = [
  { to: "/pmcc", label: "PMCC" },
  { to: "/calendars", label: "Calendars" },
  { to: "/curve", label: "Curve" },
  { to: "/contango", label: "Contango" },
  { to: "/flies", label: "Flies" },
];

export const CONFIG_LINK: NavLinkDef = { to: "/config", label: "Config", key: "o" };

/** Suite pages the SIMPLE view leaves off the rail: the experiment comparison, the backtest, the
 *  advisor and the suite's own health are developer surfaces. Still reachable by URL and shortcut,
 *  and listed while you are on one, the same rule as a module's detail tabs. */
export const SIMPLE_HIDDEN_PAGES: readonly string[] = ["/compare", "/backtest", "/advisor", "/system"];

/** The suite links a view lists; `current` is the page the reader is on (always kept). */
export function suiteLinksFor<T extends NavLinkDef>(links: readonly T[], view: "simple" | "detail", current: string | null): T[] {
  if (view === "detail") return [...links];
  return links.filter((l) => !SIMPLE_HIDDEN_PAGES.includes(l.to) || l.to.slice(1) === current);
}

export const ALL_NAV_LINKS: readonly NavLinkDef[] = [...SUITE_LINKS, ...MODULE_LINKS, ...EXPERIMENT_LINKS, CONFIG_LINK];

/**
 * The links a reader can actually use right now: an off module (or the advisor, or GEX with its
 * recorder off) is left out of every nav. The digits are fixed (2026-10-30): an off module leaves
 * its key unbound rather than renumbering the rest. Unknown features
 * keep every link (`visibility.ts`: unknown is visible) -- except Backtest, which fails closed and is
 * listed only once the suite says `backtest: true`.
 */
export function visibleNavLinks(features: SuiteFeatures | undefined): {
  suite: NavLinkDef[];
  modules: NavLinkDef[];
  experiments: NavLinkDef[];
} {
  const shown = (l: NavLinkDef) => isModuleVisible(l.to.slice(1), features);
  return {
    suite: SUITE_LINKS.filter(shown),
    modules: MODULE_LINKS.filter(shown),
    experiments: EXPERIMENT_LINKS.filter(shown),
  };
}
