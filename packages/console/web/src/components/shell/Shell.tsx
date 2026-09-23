import { Outlet, useLocation } from "react-router-dom";
import { StatusHeader } from "./StatusHeader";
import { ToastStack } from "./ToastStack";
import { isModuleId } from "../../lightbox/moduleOrder";
import { isFrameModule } from "../../lightbox/registry";
import { useBoolPref, usePrefsSync } from "../../lib/prefs";
import { useTradeNotifications } from "../../lib/useTradeNotifications";

export function Shell() {
  const location = useLocation();
  // A frame module owns the full content area: the rail sits against the header with no gutter,
  // and its own body scrolls rather than the shell's.
  const seg = location.pathname.split("/")[1] ?? "";
  const framed = isModuleId(seg) && isFrameModule(seg);
  // Pull the server's copy once, then let every reader work off the synchronous mirror.
  usePrefsSync();
  const dense = useBoolPref("denseTables");
  // Mounted once here (not per-page) so trade toasts fire regardless of which page is open.
  useTradeNotifications();
  return (
    <div className={dense ? "shell dense" : "shell"}>
      <StatusHeader />
      <main className={framed ? "shell-content shell-content-flush" : "shell-content"}>
        {/* Keyed by the MODULE, not the full pathname. The key exists to remount on navigation so
            the CSS fade retriggers, but a module's slide lives in the path too (`/flies/forest`),
            so keying on the whole thing remounted the module -- and refetched everything it had --
            on every tab click. That is the defect the lightbox's own body fixed in 2026-09 by
            staying mounted and handing off, and keying here undid it one level up. A tab change
            now reconciles; a module change still remounts and fades. */}
        <div key={seg} className={framed ? "view-fade view-fill" : "view-fade"}>
          <Outlet />
        </div>
      </main>
      <ToastStack />
    </div>
  );
}
