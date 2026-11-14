import { OneChartPage } from "../../pages/Chart/OneChartPage";
import { ProfilePage } from "../../pages/Profile/ProfilePage";
import { SetupsPage } from "../../pages/Setups/SetupsPage";
import { ModuleFrame } from "../ModuleFrame";
import type { SlideDef } from "../types";

/**
 * The suite's charts: one chart for every symbol (`/charts/chart?symbol=/ES&tf=5m`: the live intraday
 * candles with a Market Profile overlay, or the technicals package's daily chart), the recorded
 * sessions' Market Profiles side by side, and the setups watchlist over every technicals chart
 * (`/charts/setups`). This manifest holds the page list and nothing else.
 */
export const slides: SlideDef[] = [
  { id: "chart", label: "chart", render: () => <OneChartPage /> },
  { id: "profile", label: "profile", render: () => <ProfilePage /> },
  { id: "setups", label: "setups", render: () => <SetupsPage /> },
];

export function ChartsLightbox({ slide }: { slide: string }) {
  return <ModuleFrame module="charts" slide={slide} slides={slides} session={null} />;
}
