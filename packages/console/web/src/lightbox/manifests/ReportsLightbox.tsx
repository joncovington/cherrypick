import { ExternalReportsPage } from "../../pages/External/ExternalReportsPage";
import { MorningPage } from "../../pages/Morning/MorningPage";
import { ReviewPage } from "../../pages/Review/ReviewPage";
import { ModuleFrame } from "../ModuleFrame";
import type { SlideDef } from "../types";

/**
 * The suite's session reports, on the module frame since 2026-09-25: the pre-open morning pack and
 * the end-of-day review, one page each, and (2026-11-08) the reports a person's own scripts write.
 * They stay separate artifacts written by separate packages (`packages/overview`,
 * `packages/review`; `data/external/` for the third) rendered by their own unchanged page components
 * -- this manifest holds the page list and nothing else. `/reports/eod` is the review; `/review`
 * redirects there.
 */
export const slides: SlideDef[] = [
  { id: "morning", label: "morning", render: () => <MorningPage /> },
  { id: "eod", label: "eod", render: () => <ReviewPage /> },
  { id: "external", label: "external", render: () => <ExternalReportsPage /> },
];

export function ReportsLightbox({ slide }: { slide: string }) {
  return <ModuleFrame module="reports" slide={slide} slides={slides} session={null} />;
}
