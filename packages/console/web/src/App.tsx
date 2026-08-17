import { Routes, Route } from "react-router-dom";
import { ReviewPage } from "./pages/Review/ReviewPage";
import { MorningPage } from "./pages/Morning/MorningPage";
import { AdvisorPage } from "./pages/Advisor/AdvisorPage";
import { Shell } from "./components/shell/Shell";
import { OverviewPage } from "./pages/Overview/OverviewPage";
import { MeicPage } from "./pages/Meic/MeicPage";
import { ChampionsPage } from "./pages/Champions/ChampionsPage";
import { FliesPage } from "./pages/Flies/FliesPage";
import { EarningsPage } from "./pages/Earnings/EarningsPage";
import { GexPage } from "./pages/Gex/GexPage";
import { WatchlistPage } from "./pages/Scout/WatchlistPage";
import { SymbolPage } from "./pages/Scout/SymbolPage";
import { BuilderPage } from "./pages/Scout/BuilderPage";
import { OrdersPage } from "./pages/Scout/OrdersPage";
import { ScreenerPage } from "./pages/Scout/ScreenerPage";
import { ConfigPage } from "./pages/Config/ConfigPage";
import { PlaceholderPage } from "./pages/PlaceholderPage";

export default function App() {
  return (
    <Routes>
      <Route element={<Shell />}>
        <Route index element={<OverviewPage />} />
        <Route path="morning" element={<MorningPage />} />
        <Route path="review" element={<ReviewPage />} />
        <Route path="advisor" element={<AdvisorPage />} />
        <Route path="meic" element={<MeicPage />} />
        <Route path="flies" element={<FliesPage />} />
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
      </Route>
    </Routes>
  );
}
