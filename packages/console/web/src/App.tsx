import { Routes, Route, Navigate, useLocation } from "react-router-dom";
import { Shell } from "./components/shell/Shell";
import { OverviewPage } from "./pages/Overview/OverviewPage";
import { ModuleRoute } from "./pages/ModuleRoute";
import { NotFoundPage } from "./pages/NotFoundPage";
import { FlowPostPage } from "./pages/Flow/FlowPostPage";

/** A moved page whose links carry a query (`/reports/chart?symbol=MSFT`): the query goes with it. */
function MovedTo({ to }: { to: string }) {
  const { search } = useLocation();
  return <Navigate to={`${to}${search}`} replace />;
}

export default function App() {
  return (
    <Routes>
      {/* The Discord series' capture page: outside the shell (no header, no rail) and in no nav. */}
      <Route path="post/flow" element={<FlowPostPage />} />
      <Route element={<Shell />}>
        <Route index element={<OverviewPage />} />
        {/* Pre-2026-09 routes that appear in the suite's own docs — redirect rather than 404, and
            `replace` so Back does not bounce off the old URL. `/reports?tab=eod` is now the `eod`
            page, matching every other page's page-in-the-URL convention. */}
        <Route path="morning" element={<Navigate to="/reports" replace />} />
        <Route path="review" element={<Navigate to="/reports/eod" replace />} />
        {/* The technicals chart moved to the Charts page (2026-10-01); morning-pack links and
            bookmarks still say `/reports/chart?symbol=X`. A static segment outranks `:module/:slide`. */}
        <Route path="reports/chart" element={<MovedTo to="/charts/technicals" />} />
        {/* Champions & challengers was REMOVED 2026-08-20 — judging whether an arm earned
            anything belongs to the advisor's experiments now. Redirected rather than left to the
            generic 404, which tells the reader their build is stale and to reload: true for a
            missing route, actively misleading for a deliberately removed one. */}
        <Route path="champions" element={<Navigate to="/advisor" replace />} />
        {/* Every trading module AND the suite-level surfaces (GEX, Live, Reports, Advisor,
            Config) live under these two routes, each a rail and a content pane inside the shell
            (`ModuleRoute`, `registry.ts`). An unknown name 404s. */}
        <Route path=":module" element={<ModuleRoute />} />
        <Route path=":module/:slide" element={<ModuleRoute />} />
        {/* Catch-all. Without it an unmatched path renders NOTHING — a blank screen that reads as
            a crashed app, which is what a tab open across a rebuild sees when it asks the old
            bundle for a route only the new one has. */}
        <Route path="*" element={<NotFoundPage />} />
      </Route>
    </Routes>
  );
}
