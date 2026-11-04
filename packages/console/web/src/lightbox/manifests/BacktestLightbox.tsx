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
  // What we tested and what we found (2026-10-29), first from 2026-11-03: one card per study, its
  // run's figures and its queue rows in the lightbox it opens.
  { id: "studies", label: "studies", render: () => <BacktestStudiesTab /> },
  // What the queue is doing now (2026-10-24): running, queued, recent failures, runs by hand.
  { id: "queue", label: "queue", render: () => <BacktestQueueTab /> },
  { id: "runs", label: "runs", render: () => <BacktestRunsTab /> },
  // Validation lives here from 2026-10-30, as "is history trusted?" with its tables behind an expand;
  // slices moved to Compare.
  { id: "data", label: "data", render: () => <BacktestDataTab /> },
];

export function BacktestLightbox({ slide }: { slide: string }) {
  return <ModuleFrame module="backtest" slide={slide} slides={slides} session={null} />;
}
