import { Outlet, useLocation } from "react-router-dom";
import { StatusHeader } from "./StatusHeader";
import { StatusBar } from "./StatusBar";
import { PageErrorBoundary } from "./PageErrorBoundary";
import { ToastStack } from "./ToastStack";
import { ModuleNav } from "./ModuleNav";
import { useRailExpanded } from "./railState";
import { isModuleId, type ModuleId } from "../../lightbox/moduleOrder";
import { useEffect } from "react";
import { useBoolPref, usePrefsSync, usePrefsVersion, getPref, prefWritesSettled } from "../../lib/prefs";
import { useTradeNotifications } from "../../lib/useTradeNotifications";
import { useKeyboardNav } from "../../lib/useKeyboardNav";

export function Shell() {
  const location = useLocation();
  // Every page is framed now (2026-10-11): the rail is the ONE nav and renders HERE, on the shell,
  // for every route — Overview included — instead of each frame page mounting its own copy beside
  // the content (the duplication the hamburger menu used to be). The current module's segment
  // decides whether the rail opens out that module's tabs.
  const seg = location.pathname.split("/")[1] ?? "";
  const module: ModuleId | null = isModuleId(seg) ? (seg as ModuleId) : null;
  const [railExpanded] = useRailExpanded();
  // Pull the server's copy once, then let every reader work off the synchronous mirror.
  usePrefsSync();
  const dense = useBoolPref("denseTables");
  // The theme rides the same mirror. The boot script in index.html set the attribute before
  // first paint; this keeps it in step when the pref arrives from the server or changes. A flip
  // mid-session reloads — both directions — because the canvas palette consts (chart/cssvars.ts,
  // the candlestick pages) read :root at module import: without the reload the CSS-derived
  // surfaces flip and the charts keep the old palette. One reload beats half-flipped charts.
  // The version subscription is what re-renders this component when the mirror publishes; the
  // theme itself is read off it synchronously, same as denseTables.
  usePrefsVersion();
  const theme = getPref("theme") === "light" ? "light" : "dark";
  useEffect(() => {
    const el = document.documentElement;
    if (el.dataset.theme === theme) return;
    if (el.dataset.theme !== undefined) {
      el.dataset.theme = theme;
      // After the pref's own POST lands: reloading first aborted it, and the next load's sync
      // pulled the old theme back.
      void prefWritesSettled().then(() => window.location.reload());
      return;
    }
    el.dataset.theme = theme;
  }, [theme]);
  // Mounted once here (not per-page) so trade toasts fire regardless of which page is open.
  useTradeNotifications();
  // Likewise: the chords (`g o`, `1`–`9`, `j`/`k`) work from every page.
  useKeyboardNav();
  return (
    <div className={dense ? (railExpanded ? "shell dense" : "shell dense shell-rail-collapsed") : railExpanded ? "shell" : "shell shell-rail-collapsed"}>
      {/* The grid's first column is the rail, spanning the header and content rows: its right
          border is one unbroken vertical from the top of the window to the status bar, and the
          brand block lives in the rail's head, inside that line. The top bar sits beside it in
          column 2 and cannot leave a notch where they meet. Collapsed (the default since
          2026-10-01) the first column folds to 64px and the wordmark to the cherry alone. The
          toggle lives at the rail's foot; nothing else opens or closes it. */}
      <ModuleNav module={module} slide={location.pathname.split("/")[2] ?? ""} />
      <StatusHeader />
      <main className={module !== null ? "shell-content shell-content-flush" : "shell-content"}>
        {/* Keyed by the MODULE, not the full pathname. The key exists to remount on navigation so
            the CSS fade retriggers, but a module's slide lives in the path too (`/flies/forest`),
            so keying on the whole thing remounted the module -- and refetched everything it had --
            on every tab click. That is the defect the old lightbox's own body fixed in 2026-09 by
            staying mounted and handing off, and keying here undid it one level up. A tab change
            now reconciles; a module change still remounts and fades. */}
        <div key={seg} className={module !== null ? "view-fade view-fill" : "view-fade"}>
          <PageErrorBoundary resetKey={location.pathname}>
            <Outlet />
          </PageErrorBoundary>
        </div>
      </main>
      <StatusBar />
      <ToastStack />
    </div>
  );
}
