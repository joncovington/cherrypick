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
}

export const SUITE_LINKS: readonly NavLinkDef[] = [
  { to: "/charts", label: "Charts", key: "c" },
  { to: "/flow", label: "Options flow", key: "f" },
  { to: "/reports", label: "Reports", key: "r" },
  { to: "/compare", label: "Compare", key: "m" },
  { to: "/advisor", label: "Advisor", key: "a" },
  { to: "/live", label: "Live", key: "l" },
  { to: "/system", label: "System", key: "s" },
];

/** The module pages, in the order the suite lists them everywhere (`TRADING_MODULE_ORDER`, then
 *  GEX). Hand-kept for its labels and keys, so `test/navLinks.test.ts` fails when a trading module
 *  has no link here: contango shipped 2026-10-05 with a route and no way to reach it. */
export const MODULE_LINKS: readonly NavLinkDef[] = [
  { to: "/meic", label: "MEIC", key: "1" },
  { to: "/flies", label: "Flies", key: "2" },
  { to: "/pmcc", label: "PMCC", key: "3" },
  { to: "/curve", label: "Curve", key: "4" },
  { to: "/contango", label: "Contango", key: "5" },
  { to: "/bwb", label: "BWB", key: "6" },
  { to: "/calendars", label: "Calendars", key: "7" },
  { to: "/earnings", label: "Earnings", key: "8" },
  { to: "/gex", label: "GEX", key: "9" },
];

export const CONFIG_LINK: NavLinkDef = { to: "/config", label: "Config" };

/** Suite pages the SIMPLE view leaves off the rail: the experiment comparison, the advisor and the
 *  suite's own health are developer surfaces. Still reachable by URL and shortcut, and listed while
 *  you are on one, the same rule as a module's detail tabs. */
export const SIMPLE_HIDDEN_PAGES: readonly string[] = ["/compare", "/advisor", "/system"];

/** The suite links a view lists; `current` is the page the reader is on (always kept). */
export function suiteLinksFor<T extends NavLinkDef>(links: readonly T[], view: "simple" | "detail", current: string | null): T[] {
  if (view === "detail") return [...links];
  return links.filter((l) => !SIMPLE_HIDDEN_PAGES.includes(l.to) || l.to.slice(1) === current);
}

export const ALL_NAV_LINKS: readonly NavLinkDef[] = [...SUITE_LINKS, ...MODULE_LINKS, CONFIG_LINK];

/**
 * The links a reader can actually use right now: an off module (or the advisor, or GEX with its
 * recorder off) is left out of every nav, and the digit shortcuts are renumbered over what is left,
 * so `1`–`n` always name the modules the menu shows in the order it shows them. Unknown features
 * keep every link (`visibility.ts`: unknown is visible).
 */
export function visibleNavLinks(features: SuiteFeatures | undefined): {
  suite: NavLinkDef[];
  modules: NavLinkDef[];
} {
  const shown = (l: NavLinkDef) => isModuleVisible(l.to.slice(1), features);
  return {
    suite: SUITE_LINKS.filter(shown),
    modules: MODULE_LINKS.filter(shown).map((l, i) => ({ ...l, key: String(i + 1) })),
  };
}
