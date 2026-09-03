import { Routes, Route, Navigate } from "react-router-dom";
import { ReportsPage } from "./pages/Reports/ReportsPage";
import { AdvisorPage } from "./pages/Advisor/AdvisorPage";
import { Shell } from "./components/shell/Shell";
import { OverviewPage } from "./pages/Overview/OverviewPage";
import { OverviewWithLightbox } from "./pages/Overview/OverviewWithLightbox";
import { GexPage } from "./pages/Gex/GexPage";
import { ConfigPage } from "./pages/Config/ConfigPage";
import { NotFoundPage } from "./pages/NotFoundPage";

export default function App() {
  return (
    <Routes>
      <Route element={<Shell />}>
        <Route index element={<OverviewPage />} />
        <Route path="reports" element={<ReportsPage />} />
        {/* Both routes predate the Reports page and appear in the suite's own docs — redirect
            rather than 404, and `replace` so Back does not bounce off the old URL. */}
        <Route path="morning" element={<Navigate to="/reports" replace />} />
        <Route path="review" element={<Navigate to="/reports?tab=eod" replace />} />
        {/* Champions & challengers was REMOVED 2026-08-20 — judging whether an arm earned
            anything belongs to the advisor's experiments now. Redirected rather than left to the
            generic 404, which tells the reader their build is stale and to reload: true for a
            missing route, actively misleading for a deliberately removed one. */}
        <Route path="champions" element={<Navigate to="/advisor" replace />} />
        <Route path="advisor" element={<AdvisorPage />} />
        {/* GEX and Advisor are suite-level surfaces, not module books -- they keep their own
            tabbed pages. Every trading module below opens as a carousel over the Overview
            (`OverviewWithLightbox`); an unknown module name still 404s. */}
        <Route path="gex" element={<GexPage />} />
        <Route path="config" element={<ConfigPage />} />
        <Route path=":module" element={<OverviewWithLightbox />} />
        <Route path=":module/:slide" element={<OverviewWithLightbox />} />
        {/* Catch-all. Without it an unmatched path renders NOTHING — a blank screen that reads as
            a crashed app, which is what a tab open across a rebuild sees when it asks the old
            bundle for a route only the new one has. */}
        <Route path="*" element={<NotFoundPage />} />
      </Route>
    </Routes>
  );
}
