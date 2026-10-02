import { Link } from "react-router-dom";
import { MODULE_LABEL, type ModuleId } from "../lightbox/moduleOrder";
import type { OffReason } from "../lib/visibility";

/**
 * What a direct URL to a module the suite has turned off renders: not a 404 (the page exists, and a
 * bookmark to it is not a stale build) and not the module (whose data stopped when it was switched
 * off, and would read as a stalled loop). It says why, in the orchestrator's words, and where the
 * switch is.
 */
export function ModuleOffCard({ module, off }: { module: ModuleId; off: OffReason }) {
  return (
    <div className="mf-layout">
      <div className="mf-body">
        <section className="card module-off">
          <div className="card-head">
            <h2>{MODULE_LABEL[module]} is turned off</h2>
          </div>
          <p>{off.reason}</p>
          <p className="muted">
            Nothing from it is shown anywhere in the console while it is off. Turn it back on from{" "}
            <Link className="link" to="/config">Config</Link>.
          </p>
        </section>
      </div>
    </div>
  );
}
