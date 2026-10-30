import { ExperimentsPage } from "../../pages/Experiments/ExperimentsPage";
import { ModuleFrame } from "../ModuleFrame";
import type { SlideDef } from "../types";

/** The Experiments page (2026-10-30): one tab, a card per paper-only experiment and the flies record. */
export const slides: SlideDef[] = [{ id: "experiments", label: "experiments", render: () => <ExperimentsPage /> }];

export function ExperimentsLightbox({ slide }: { slide: string }) {
  return <ModuleFrame module="experiments" slide={slide} slides={slides} session={null} />;
}
