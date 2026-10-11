import { MorningPage } from "../../pages/Morning/MorningPage";
import { ReviewPage } from "../../pages/Review/ReviewPage";
import { SummaryPage } from "../../pages/Reports/SummaryPage";
import { ModuleFrame } from "../ModuleFrame";
import type { SlideDef } from "../types";

/**
 * The suite's session reports and the trade summary, on the module frame since 2026-09-25: the
 * pre-open morning pack, the end-of-day review, and — since 2026-10-11 — the suite-wide trade
 * summary (the MEIC prototype's money cards over every module's `core.metrics summary` slice).
 * The report pages stay separate artifacts written by separate packages (`packages/overview`,
 * `packages/review`) rendered by their own unchanged page components; the summary page is the
 * exploration surface over the SAME numbers the review promotes one verdict per session.
 * `/reports/eod` is the review; `/review` redirects there.
 */
const slides: SlideDef[] = [
  { id: "morning", label: "morning", render: () => <MorningPage /> },
  { id: "eod", label: "eod", render: () => <ReviewPage /> },
  { id: "summary", label: "summary", render: () => <SummaryPage /> },
];

export function ReportsLightbox({ slide }: { slide: string }) {
  return <ModuleFrame module="reports" slide={slide} slides={slides} session={null} />;
}
