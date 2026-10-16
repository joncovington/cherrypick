import { FidelityTab, ScreenerTab } from "../../pages/Screeners/ScreenersPage";
import { ModuleFrame } from "../ModuleFrame";
import type { SlideDef } from "../types";

/**
 * The income screener lists (2026-10-16): one tab per list, and how well our reproduction of the
 * screen's rules matches them. This manifest holds the page list and nothing else.
 */
const slides: SlideDef[] = [
  { id: "short-puts", label: "short puts", render: () => <ScreenerTab list="short-puts" /> },
  { id: "covered-calls", label: "covered calls", render: () => <ScreenerTab list="covered-calls" /> },
  { id: "credit-spreads", label: "credit spreads", render: () => <ScreenerTab list="credit-spreads" /> },
  { id: "fidelity", label: "fidelity", render: () => <FidelityTab /> },
];

export function ScreenersLightbox({ slide }: { slide: string }) {
  return <ModuleFrame module="screeners" slide={slide} slides={slides} session={null} />;
}
