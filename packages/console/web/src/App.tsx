import { Routes, Route, Navigate, useLocation, useParams } from "react-router-dom";
import { Shell } from "./components/shell/Shell";
import { ModuleRoute } from "./pages/ModuleRoute";
import { NotFoundPage } from "./pages/NotFoundPage";
import { movedChartUrl, type MovedChartTab } from "./pages/Chart/chartRoute";

/** A moved page whose links carry a query (`/reports/chart?symbol=MSFT`): the query goes with it. */
function MovedTo({ to }: { to: string }) {
  const { search } = useLocation();
  return <Navigate to={`${to}${search}`} replace />;
}

/** Where a renamed module's old address goes: the new page, with the same tab and query. */
export function renamedTarget(to: string, slide: string | undefined, search: string): string {
  return `${to}${slide ? `/${slide}` : ""}${search}`;
}

/** A renamed module's page (`/ensemble/positions` before automations), its tab and query kept. */
function RenamedModule({ to }: { to: string }) {
  const { slide } = useParams();
  const { search } = useLocation();
  return <Navigate to={renamedTarget(to, slide, search)} replace />;
}

/** An old chart URL onto the one chart, its query translated (`movedChartUrl`). */
function MovedChart({ from }: { from: MovedChartTab }) {
  const { search } = useLocation();
  return <Navigate to={movedChartUrl(from, search)} replace />;
}

function HelpRoute() {
  const { slug } = useParams();
  return <ModuleRoute forced={{ module: "help", slide: slug === "faq" ? "faq" : "guides" }} />;
}

export default function App() {
  return (
    <Routes>
      <Route element={<Shell />}>
        {/* Home is the Live page (2026-10-16): the Overview page was removed, and `/` goes where
            the real account's day is read. `replace`, so Back does not bounce off `/`. */}
        <Route index element={<Navigate to="/live" replace />} />
        {/* Pre-2026-09 routes that appear in the suite's own docs — redirect rather than 404, and
            `replace` so Back does not bounce off the old URL. `/reports?tab=eod` is now the `eod`
            page, matching every other page's page-in-the-URL convention. */}
        <Route path="morning" element={<Navigate to="/reports" replace />} />
        <Route path="review" element={<Navigate to="/reports/eod" replace />} />
        {/* The intraday, technicals and profile tabs are one chart (`/charts/chart`); links and
            bookmarks still say `/charts/intraday?product=ES`, `/charts/technicals?symbol=X`,
            `/charts/profile?symbol=X&day=D` and the older `/reports/chart?symbol=X`. A static segment
            outranks `:module/:slide`. */}
        <Route path="charts/intraday" element={<MovedChart from="intraday" />} />
        <Route path="charts/technicals" element={<MovedChart from="technicals" />} />
        <Route path="charts/profile" element={<MovedChart from="profile" />} />
        <Route path="reports/chart" element={<MovedChart from="technicals" />} />
        {/* The slice reports moved from Backtest to Compare (2026-10-30). Compare's tab fails closed on
            the same feature, so with it off the redirect lands on Compare's first tab. */}
        <Route path="backtest/slices" element={<MovedTo to="/compare/slices" />} />
        {/* The ensemble page is the automations page (#1200); old links and bookmarks follow it. */}
        <Route path="ensemble" element={<RenamedModule to="/automations" />} />
        <Route path="ensemble/:slide" element={<RenamedModule to="/automations" />} />
        {/* The Builder is Automations' Builder tab (2026-11-20); its old page, tab and
            `?automation=` links follow it there. */}
        <Route path="builder" element={<MovedTo to="/automations/builder" />} />
        <Route path="builder/:slide" element={<MovedTo to="/automations/builder" />} />
        {/* Champions & challengers was REMOVED 2026-08-20 — judging whether an arm earned
            anything belongs to the advisor's experiments now. Redirected rather than left to the
            generic 404, which tells the reader their build is stale and to reload: true for a
            missing route, actively misleading for a deliberately removed one. */}
        <Route path="champions" element={<Navigate to="/advisor" replace />} />
        {/* Every trading module AND the suite-level surfaces (GEX, Live, Reports, Advisor,
            Config) live under these two routes, each a rail and a content pane inside the shell
            (`ModuleRoute`, `registry.ts`). An unknown name 404s. */}
        {/* One report of External reports (data-driven tab, `ExternalReportsPage`): the frame is Reports / external. */}
        <Route path="reports/external/:reportId" element={<ModuleRoute forced={{ module: "reports", slide: "external" }} />} />
        {/* One guide page (`/help/<slug>`), inside Help's `guides` tab; `/help/faq` is the FAQ tab. A guide's
            slug is not a tab id, so the generic route would rewrite it to the first tab. */}
        <Route path="help/:slug" element={<HelpRoute />} />
        {/* One automation's page (`/automations/automation/<name>`), framed as Automations › groups,
            where every automation is listed. */}
        <Route path="automations/automation/:name" element={<ModuleRoute forced={{ module: "automations", slide: "groups" }} />} />
        {/* One MEIC arm's page (`/meic/arm/<name>`), framed as MEIC › arms. */}
        <Route path="meic/arm/:name" element={<ModuleRoute forced={{ module: "meic", slide: "arms" }} />} />
        {/* One arm's page in a module on the shared slides, framed as its arms tab. */}
        <Route path="calendars/arm/:name" element={<ModuleRoute forced={{ module: "calendars", slide: "arms" }} />} />
        <Route path="contango/arm/:name" element={<ModuleRoute forced={{ module: "contango", slide: "arms" }} />} />
        <Route path="ladder/arm/:name" element={<ModuleRoute forced={{ module: "ladder", slide: "arms" }} />} />
        {/* Earnings has no arms tab: its arms' pages sit under performance. */}
        <Route path="earnings/arm/:name" element={<ModuleRoute forced={{ module: "earnings", slide: "performance" }} />} />
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
