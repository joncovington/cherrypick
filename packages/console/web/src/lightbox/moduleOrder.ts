/**
 * The seven trading modules, in the same order the header menu lists them
 * (`components/shell/HeaderMenu.tsx`) -- one order, so "next slide" at a module's last slide and
 * the nav dropdown agree about what "next" means.
 */
export const TRADING_MODULE_ORDER = ["meic", "flies", "pmcc", "curve", "bwb", "calendars", "earnings"] as const;

export type TradingModuleId = (typeof TRADING_MODULE_ORDER)[number];

/**
 * The suite-level surfaces (2026-09): not module books, but given the same lightbox carousel
 * treatment as the trading modules -- overlay, slide rail, keyboard nav -- rather than a separate
 * standalone-page style. They sit after the trading modules in the carousel ring, so stepping past
 * Earnings reaches GEX rather than wrapping straight back to MEIC.
 */
export const SUITE_ORDER = ["gex", "reports", "advisor", "config"] as const;

export type SuiteId = (typeof SUITE_ORDER)[number];

/** Every id the lightbox carousel and its ring (next/prev module) know how to open. */
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
  reports: "Reports",
  advisor: "Advisor",
  config: "Config",
};
