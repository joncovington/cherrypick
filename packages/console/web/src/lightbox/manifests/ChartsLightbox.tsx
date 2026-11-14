import { IntradayPage } from "../../pages/Intraday/IntradayPage";
import { ProfilePage } from "../../pages/Profile/ProfilePage";
import { ChartPage } from "../../pages/Morning/ChartPage";
import { SetupsPage } from "../../pages/Setups/SetupsPage";
import { ModuleFrame } from "../ModuleFrame";
import type { SlideDef } from "../types";

/**
 * The suite's charts: the live intraday chart (futures plus SPX, SPY, NDX and QQQ, with a Market
 * Profile overlay) over the console's own DXLink session, and
 * the technicals package's one-name chart (`/charts/technicals?symbol=MSFT`), which was Reports'
 * `chart` tab until 2026-10-01, and the setups watchlist over every technicals chart
 * (`/charts/setups`). This manifest holds the page list and nothing else.
 */
export const slides: SlideDef[] = [
  { id: "intraday", label: "intraday", render: () => <IntradayPage /> },
  { id: "profile", label: "profile", render: () => <ProfilePage /> },
  { id: "technicals", label: "technicals", render: () => <ChartPage /> },
  { id: "setups", label: "setups", render: () => <SetupsPage /> },
];

export function ChartsLightbox({ slide }: { slide: string }) {
  return <ModuleFrame module="charts" slide={slide} slides={slides} session={null} />;
}
