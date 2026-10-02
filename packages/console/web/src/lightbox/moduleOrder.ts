/** The seven trading modules, in the order the suite lists them. */
export const TRADING_MODULE_ORDER = ["meic", "flies", "pmcc", "curve", "bwb", "calendars", "earnings"] as const;

export type TradingModuleId = (typeof TRADING_MODULE_ORDER)[number];

/**
 * The suite-level surfaces: not module books, but pages on the same frame as the trading modules
 * (since 2026-09-25) -- a rail of tabs and a content pane -- rather than a separate page style.
 */
export const SUITE_ORDER = ["gex", "live", "charts", "reports", "advisor", "system", "config"] as const;

export type SuiteId = (typeof SUITE_ORDER)[number];

/** Every page on the frame: `/:module` and `/:module/:slide`. */
export const MODULE_ORDER = [...TRADING_MODULE_ORDER, ...SUITE_ORDER] as const;

export type ModuleId = (typeof MODULE_ORDER)[number];

export function isModuleId(v: string): v is ModuleId {
  return (MODULE_ORDER as readonly string[]).includes(v);
}

export function isTradingModuleId(v: string): v is TradingModuleId {
  return (TRADING_MODULE_ORDER as readonly string[]).includes(v);
}

export const MODULE_LABEL: Record<ModuleId, string> = {
  meic: "MEIC",
  flies: "Flies",
  pmcc: "PMCC-99",
  curve: "curve",
  bwb: "bwb",
  calendars: "Calendars",
  earnings: "Earnings",
  gex: "GEX",
  live: "Live",
  charts: "Charts",
  reports: "Reports",
  advisor: "Advisor",
  system: "System",
  config: "Config",
};

/**
 * The modules the suite calls EXPERIMENTAL (2026-10-01): paper-only designs still being shaped.
 * One list, read by the rail, the header menu and each page's title, so the chip cannot appear in
 * one place and not another.
 */
export const EXPERIMENTAL_MODULES: ReadonlySet<string> = new Set<ModuleId>(["calendars", "pmcc", "curve"]);

export function isExperimental(id: string): boolean {
  return EXPERIMENTAL_MODULES.has(id);
}
