import { MorningPage } from "../../pages/Morning/MorningPage";
import { ReviewPage } from "../../pages/Review/ReviewPage";
import { LightboxFrame } from "../LightboxFrame";
import type { SlideDef } from "../types";

/**
 * The suite's two session reports as a lightbox (2026-09): the pre-open morning pack and the
 * end-of-day review, given the same overlay/rail/keyboard-nav treatment as the trading modules.
 * They stay separate artifacts written by separate packages (`packages/overview`,
 * `packages/review`) rendered by their own unchanged page components -- this lightbox holds the
 * slide and nothing else, same as the old `ReportsPage` held only the tab. The old `?tab=eod`
 * query-param convention is now the `eod` slide id (`/reports/eod`), matching every other
 * lightbox's own slide-in-the-URL convention.
 */
const slides: SlideDef[] = [
  { id: "morning", label: "Morning", render: () => <MorningPage /> },
  { id: "eod", label: "EOD", render: () => <ReviewPage /> },
];

export function ReportsLightbox({ slide }: { slide: string }) {
  return <LightboxFrame module="reports" slide={slide} slides={slides} session={null} />;
}
