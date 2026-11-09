import { FaqTab } from "../../pages/Help/FaqTab";
import { GuidesTab } from "../../pages/Help/GuidesTab";
import { ModuleFrame } from "../ModuleFrame";
import type { SlideDef } from "../types";

/**
 * Help (2026-11-09): the user guides in docs/guide, served from this checkout, so the guide always
 * matches the code it describes. `guides` holds every page (`/help/<slug>`), `faq` the questions.
 */
export const slides: SlideDef[] = [
  { id: "guides", label: "guides", render: () => <GuidesTab /> },
  { id: "faq", label: "faq", render: () => <FaqTab /> },
];

export function HelpLightbox({ slide }: { slide: string }) {
  return <ModuleFrame module="help" slide={slide} slides={slides} session={null} />;
}
