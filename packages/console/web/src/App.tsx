import { Routes, Route, Navigate } from "react-router-dom";
import { ReportsPage } from "./pages/Reports/ReportsPage";
import { AdvisorPage } from "./pages/Advisor/AdvisorPage";
import { Shell } from "./components/shell/Shell";
import { OverviewPage } from "./pages/Overview/OverviewPage";
import { MeicPage } from "./pages/Meic/MeicPage";
import { ChampionsPage } from "./pages/Champions/ChampionsPage";
import { FliesPage } from "./pages/Flies/FliesPage";
import { PmccPage } from "./pages/Pmcc/PmccPage";
import { EarningsPage } from "./pages/Earnings/EarningsPage";
import { GexPage } from "./pages/Gex/GexPage";
import { WatchlistPage } from "./pages/Scout/WatchlistPage";
import { SymbolPage } from "./pages/Scout/SymbolPage";
import { BuilderPage } from "./pages/Scout/BuilderPage";
import { OrdersPage } from "./pages/Scout/OrdersPage";
import { ScreenerPage } from "./pages/Scout/ScreenerPage";
import { ConfigPage } from "./pages/Config/ConfigPage";
import { PlaceholderPage } from "./pages/PlaceholderPage";
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
        <Route path="advisor" element={<AdvisorPage />} />
        <Route path="meic" element={<MeicPage />} />
        <Route path="flies" element={<FliesPage />} />
        <Route path="pmcc" element={<PmccPage />} />
        <Route path="earnings" element={<EarningsPage />} />
        <Route path="gex" element={<GexPage />} />
        <Route path="champions" element={<ChampionsPage />} />
        <Route path="scout" element={<WatchlistPage />} />
        <Route path="scout/symbol/:symbol" element={<SymbolPage />} />
        <Route path="scout/builder" element={<BuilderPage />} />
        <Route path="scout/orders" element={<OrdersPage />} />
        <Route path="scout/screener" element={<ScreenerPage />} />
        <Route path="scout/*" element={<PlaceholderPage title="Scout" />} />
        <Route path="config" element={<ConfigPage />} />
        {/* Catch-all. Without it an unmatched path renders NOTHING — a blank screen that reads as
            a crashed app, which is what a tab open across a rebuild sees when it asks the old
            bundle for a route only the new one has. */}
        <Route path="*" element={<NotFoundPage />} />
      </Route>
    </Routes>
  );
}
