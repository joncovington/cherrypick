import { Outlet, useLocation } from "react-router-dom";
import { Nav } from "./Nav";
import { StatusHeader } from "./StatusHeader";

export function Shell() {
  const location = useLocation();
  return (
    <div className="shell">
      <Nav />
      <div className="shell-main">
        <StatusHeader />
        <main className="shell-content">
          {/* Keyed by pathname so React remounts (rather than reconciles) on navigation, which is
              what lets the CSS animation retrigger on every route change. */}
          <div key={location.pathname} className="view-fade">
            <Outlet />
          </div>
        </main>
      </div>
    </div>
  );
}
