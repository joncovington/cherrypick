import { Routes, Route, Navigate } from "react-router-dom";
import { Shell } from "./components/shell/Shell";
import { OverviewPage } from "./pages/Overview/OverviewPage";
import { OverviewWithLightbox } from "./pages/Overview/OverviewWithLightbox";
import { NotFoundPage } from "./pages/NotFoundPage";

export default function App() {
  return (
    <Routes>
      <Route element={<Shell />}>
        <Route index element={<OverviewPage />} />
        {/* Pre-2026-09 routes that appear in the suite's own docs — redirect rather than 404, and
            `replace` so Back does not bounce off the old URL. `/reports?tab=eod` is now the `eod`
            slide, matching every other lightbox's slide-in-the-URL convention. */}
        <Route path="morning" element={<Navigate to="/reports" replace />} />
        <Route path="review" element={<Navigate to="/reports/eod" replace />} />
        {/* Champions & challengers was REMOVED 2026-08-20 — judging whether an arm earned
            anything belongs to the advisor's experiments now. Redirected rather than left to the
            generic 404, which tells the reader their build is stale and to reload: true for a
            missing route, actively misleading for a deliberately removed one. */}
        <Route path="champions" element={<Navigate to="/advisor" replace />} />
        {/* Every trading module AND the suite-level surfaces (GEX, Reports, Advisor, Config --
            2026-09) open as a carousel over the Overview (`OverviewWithLightbox`); an unknown
            name still 404s via `isModuleId`'s guard inside it. */}
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
