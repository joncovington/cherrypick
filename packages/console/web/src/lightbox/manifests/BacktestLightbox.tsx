import { BacktestDataTab, BacktestRunsTab, BacktestValidationTab } from "../../pages/Backtest/BacktestPage";
import { ModuleFrame } from "../ModuleFrame";
import type { SlideDef } from "../types";

/**
 * Backtest (2026-10-17): `packages/backtest`'s replays over licensed vendor history, read through
 * `/api/backtest/*`. Reached only while the suite says `backtest: true` (`visibility.ts`, fail
 * closed); the server refuses the reads on its own otherwise. This manifest holds the page list and
 * nothing else.
 */
const slides: SlideDef[] = [
  { id: "data", label: "data", render: () => <BacktestDataTab /> },
  { id: "validation", label: "validation", render: () => <BacktestValidationTab /> },
  { id: "runs", label: "runs", render: () => <BacktestRunsTab /> },
];

export function BacktestLightbox({ slide }: { slide: string }) {
  return <ModuleFrame module="backtest" slide={slide} slides={slides} session={null} />;
}
