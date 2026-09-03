import { AdvisorPage } from "../../pages/Advisor/AdvisorPage";
import { LightboxFrame } from "../LightboxFrame";
import type { SlideDef } from "../types";

/**
 * Advisor as a lightbox (2026-09): the overlay/rail/keyboard-nav treatment every other suite-level
 * surface and trading module now gets, wrapping `AdvisorPage` UNCHANGED as this lightbox's single
 * slide rather than decomposing its own four tabs (today/proposals/experiments/history) into
 * separate lightbox slides.
 *
 * That is a deliberate, narrower scope than GEX/Reports got: this page holds the suite's only two
 * write-capable console actions (kill an experiment, dismiss a proposal --
 * `packages/console/CLAUDE.md`'s "bounded exception" section), each wired through session/tab
 * state that already spans the page (the session picker, the busy/error banner, TabSummary). Redoing
 * that wiring against a slide-driven `LightboxFrame` in the same pass as the routing migration risks
 * a subtle regression in a control path the suite deliberately keeps narrow. AdvisorPage's own
 * internal TabStrip is untouched and still switches its four views inside this one lightbox slide.
 */
const slides: SlideDef[] = [{ id: "advisor", label: "Advisor", render: () => <AdvisorPage /> }];

export function AdvisorLightbox({ slide }: { slide: string }) {
  return <LightboxFrame module="advisor" slide={slide} slides={slides} session={null} />;
}
