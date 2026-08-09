import { Routes, Route } from "react-router-dom";
import { Shell } from "./components/shell/Shell";
import { OverviewPage } from "./pages/Overview/OverviewPage";
import { MeicPage } from "./pages/Meic/MeicPage";
import { FliesPage } from "./pages/Flies/FliesPage";
import { EarningsPage } from "./pages/Earnings/EarningsPage";
import { GexPage } from "./pages/Gex/GexPage";
import { WatchlistPage } from "./pages/Scout/WatchlistPage";
import { SymbolPage } from "./pages/Scout/SymbolPage";
import { BuilderPage } from "./pages/Scout/BuilderPage";
import { OrdersPage } from "./pages/Scout/OrdersPage";
import { PlaceholderPage } from "./pages/PlaceholderPage";

export default function App() {
  return (
    <Routes>
      <Route element={<Shell />}>
        <Route index element={<OverviewPage />} />
        <Route path="meic" element={<MeicPage />} />
        <Route path="flies" element={<FliesPage />} />
        <Route path="earnings" element={<EarningsPage />} />
        <Route path="gex" element={<GexPage />} />
        <Route path="scout" element={<WatchlistPage />} />
        <Route path="scout/symbol/:symbol" element={<SymbolPage />} />
        <Route path="scout/builder" element={<BuilderPage />} />
        <Route path="scout/orders" element={<OrdersPage />} />
        <Route path="scout/*" element={<PlaceholderPage title="Scout" />} />
      </Route>
    </Routes>
  );
}
