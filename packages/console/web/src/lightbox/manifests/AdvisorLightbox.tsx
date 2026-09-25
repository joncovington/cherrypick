import { AdvisorPage } from "../../pages/Advisor/AdvisorPage";
import { ModuleFrame } from "../ModuleFrame";
import type { SlideDef } from "../types";

/**
 * Advisor on the module frame (2026-09-25; a lightbox before that), wrapping `AdvisorPage` UNCHANGED
 * as its single page rather than decomposing its own four tabs (today/proposals/experiments/
 * history) into separate frame pages.
 *
 * That is a deliberate, narrower scope than GEX/Reports got: this page holds the suite's only two
 * write-capable console actions (kill an experiment, dismiss a proposal --
 * `packages/console/CLAUDE.md`'s "bounded exception" section), each wired through session/tab
 * state that already spans the page (the session picker, the busy/error banner, TabSummary). Redoing
 * that wiring against slide-driven pages risks a subtle regression in a control path the suite
 * deliberately keeps narrow. AdvisorPage's own internal TabStrip is untouched and still switches its
 * four views inside this one page.
 */
const slides: SlideDef[] = [{ id: "advisor", label: "advisor", render: () => <AdvisorPage /> }];

export function AdvisorLightbox({ slide }: { slide: string }) {
  return <ModuleFrame module="advisor" slide={slide} slides={slides} session={null} />;
}
