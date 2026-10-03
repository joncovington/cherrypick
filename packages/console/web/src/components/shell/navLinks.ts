import type { SuiteFeatures } from "@console/shared";
import { isModuleVisible } from "../../lib/visibility";

/**
 * The suite's page list, in one place.
 *
 * Three things read it and must not disagree: the header's hamburger menu, the module frame's
 * left rail, and the keyboard handler that makes the `g o` / `1`–`8` hints in that menu do
 * something. Before the rail existed the table lived inside `HeaderMenu`, which was fine while
 * the menu was the only nav; a second nav reading a second copy is how the two end up listing
 * different modules.
 *
 * `key` is the shortcut character the menu advertises. It is advertised in exactly one place
 * (this file) and implemented in exactly one place (`lib/keyboardNav.ts`), so a hint cannot
 * survive the shortcut being dropped.
 */
export interface NavLinkDef {
  to: string;
  label: string;
  /** Exact-match route, for `/` which otherwise prefixes everything. */
  end?: boolean;
  key?: string;
}

export const SUITE_LINKS: readonly NavLinkDef[] = [
  { to: "/", label: "Overview", end: true, key: "o" },
  { to: "/charts", label: "Charts", key: "c" },
  { to: "/flow", label: "Options flow", key: "f" },
  { to: "/reports", label: "Reports", key: "r" },
  { to: "/advisor", label: "Advisor", key: "a" },
  { to: "/live", label: "Live", key: "l" },
  { to: "/system", label: "System", key: "s" },
];

/** The eight module pages, in the order the suite lists them everywhere. */
export const MODULE_LINKS: readonly NavLinkDef[] = [
  { to: "/meic", label: "MEIC", key: "1" },
  { to: "/flies", label: "Flies", key: "2" },
  { to: "/pmcc", label: "PMCC", key: "3" },
  { to: "/curve", label: "Curve", key: "4" },
  { to: "/bwb", label: "BWB", key: "5" },
  { to: "/calendars", label: "Calendars", key: "6" },
  { to: "/earnings", label: "Earnings", key: "7" },
  { to: "/gex", label: "GEX", key: "8" },
];

export const CONFIG_LINK: NavLinkDef = { to: "/config", label: "Config" };

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
