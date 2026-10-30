import { BacktestDataTab, BacktestRunsTab } from "../../pages/Backtest/BacktestPage";
import { BacktestQueueTab } from "../../pages/Backtest/QueueTab";
import { BacktestStudiesTab } from "../../pages/Backtest/StudiesTab";
import { ModuleFrame } from "../ModuleFrame";
import type { SlideDef } from "../types";

/**
 * Backtest (2026-10-17): `packages/backtest`'s replays over licensed vendor history, read through
 * `/api/backtest/*`. Reached only while the suite says `backtest: true` (`visibility.ts`, fail
 * closed); the server refuses the reads on its own otherwise. This manifest holds the page list and
 * nothing else.
 */
export const slides: SlideDef[] = [
  // The queue first (2026-10-29): what is running and what just finished is what the page is opened for.
  { id: "queue", label: "queue", render: () => <BacktestQueueTab /> },
  // What we tested and what we found (2026-10-29): a run is opened from its study.
  { id: "studies", label: "studies", render: () => <BacktestStudiesTab /> },
  { id: "runs", label: "runs", render: () => <BacktestRunsTab /> },
  // Validation lives here from 2026-10-30, as "is history trusted?" with its tables behind an expand;
  // slices moved to Compare.
  { id: "data", label: "data", render: () => <BacktestDataTab /> },
];

export function BacktestLightbox({ slide }: { slide: string }) {
  return <ModuleFrame module="backtest" slide={slide} slides={slides} session={null} />;
}
