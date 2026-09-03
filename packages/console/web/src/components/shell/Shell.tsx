import { Outlet, useLocation } from "react-router-dom";
import { StatusHeader } from "./StatusHeader";
import { ToastStack } from "./ToastStack";
import { useBoolPref, usePrefsSync } from "../../lib/prefs";
import { useTradeNotifications } from "../../lib/useTradeNotifications";

export function Shell() {
  const location = useLocation();
  // Pull the server's copy once, then let every reader work off the synchronous mirror.
  usePrefsSync();
  const dense = useBoolPref("denseTables");
  // Mounted once here (not per-page) so trade toasts fire regardless of which page is open.
  useTradeNotifications();
  return (
    <div className={dense ? "shell dense" : "shell"}>
      <StatusHeader />
      <main className="shell-content">
        {/* Keyed by pathname so React remounts (rather than reconciles) on navigation, which is
            what lets the CSS animation retrigger on every route change. */}
        <div key={location.pathname} className="view-fade">
          <Outlet />
        </div>
      </main>
      <ToastStack />
    </div>
  );
}
