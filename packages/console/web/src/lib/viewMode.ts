import { getPref, usePrefsVersion, writePref } from "./prefs";
import type { ViewMode } from "../lightbox/navGroups";

/**
 * The console's view: `simple` lists each module's own short set of tabs (its summary, its main
 * chart, performance, positions, help); `detail` lists every tab, tables and study slides included.
 * One console pref, so it holds across pages and follows the reader to the desktop shell. What
 * each view shows is each module's choice (`ModuleNavDecl.simple`), never a shared page.
 *
 * Absent means detail: the console looked like that before the view existed.
 */
export const VIEW_PREF = "viewMode";

export function readViewMode(): ViewMode {
  return getPref(VIEW_PREF) === "simple" ? "simple" : "detail";
}

export function useViewMode(): ViewMode {
  usePrefsVersion();
  return readViewMode();
}

export function setViewMode(view: ViewMode): void {
  void writePref(VIEW_PREF, view);
}
