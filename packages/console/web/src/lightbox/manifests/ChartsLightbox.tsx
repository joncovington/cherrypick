import { IntradayPage } from "../../pages/Intraday/IntradayPage";
import { ChartPage } from "../../pages/Morning/ChartPage";
import { ModuleFrame } from "../ModuleFrame";
import type { SlideDef } from "../types";

/**
 * The suite's charts: the live intraday futures chart over the console's own DXLink session, and
 * the technicals package's one-name chart (`/charts/technicals?symbol=MSFT`), which was Reports'
 * `chart` tab until 2026-10-01. This manifest holds the page list and nothing else.
 */
const slides: SlideDef[] = [
  { id: "intraday", label: "intraday", render: () => <IntradayPage /> },
  { id: "technicals", label: "technicals", render: () => <ChartPage /> },
];

export function ChartsLightbox({ slide }: { slide: string }) {
  return <ModuleFrame module="charts" slide={slide} slides={slides} session={null} />;
}
