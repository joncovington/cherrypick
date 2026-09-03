/**
 * The seven trading modules, in the same order the header menu lists them
 * (`components/shell/HeaderMenu.tsx`) -- one order, so "next slide" at a module's last slide and
 * the nav dropdown agree about what "next" means.
 */
export const MODULE_ORDER = ["meic", "flies", "pmcc", "curve", "bwb", "calendars", "earnings"] as const;

export type ModuleId = (typeof MODULE_ORDER)[number];

export function isModuleId(v: string): v is ModuleId {
  return (MODULE_ORDER as readonly string[]).includes(v);
}

export const MODULE_LABEL: Record<ModuleId, string> = {
  meic: "MEIC",
  flies: "Flies",
  pmcc: "PMCC-99",
  curve: "curve",
  bwb: "bwb",
  calendars: "Calendars",
  earnings: "Earnings",
};
