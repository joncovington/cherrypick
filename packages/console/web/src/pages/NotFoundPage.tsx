import { Link, useLocation } from "react-router-dom";

/**
 * The catch-all. It exists because the app had none, and the failure mode that produces was worse
 * than a missing page: `<Routes>` with nothing matching renders NOTHING, so an unknown path came
 * out as a blank screen with no error, no message and no way back. That is indistinguishable from a
 * crashed app, and it is exactly what a tab left open across a rebuild sees when it asks the old
 * bundle for a route that only exists in the new one.
 *
 * So this page names the path it could not match and says the one thing that fixes the common
 * case — reload, because the app in this tab may be older than the app on disk.
 */
export function NotFoundPage() {
  const { pathname } = useLocation();
  return (
    <div className="page">
      <div className="page-title-row">
        <h1>Page not found</h1>
        <span className="chip chip-missing">404</span>
      </div>
      <section className="card">
        <p>
          Nothing is routed at <code>{pathname}</code>.
        </p>
        <p className="muted">
          If you followed a link that used to work, this tab may be running an older build than the
          one on disk — reload the page and try again. Otherwise, head back to the{" "}
          <Link to="/">overview</Link> or the <Link to="/reports">reports</Link>.
        </p>
      </section>
    </div>
  );
}
