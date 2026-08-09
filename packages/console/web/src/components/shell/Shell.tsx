import { Outlet } from "react-router-dom";
import { Nav } from "./Nav";
import { StatusHeader } from "./StatusHeader";

export function Shell() {
  return (
    <div className="shell">
      <Nav />
      <div className="shell-main">
        <StatusHeader />
        <main className="shell-content">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
