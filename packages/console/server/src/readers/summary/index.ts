import type { SummaryReader } from "./common.js";
import { readFliesSummary } from "./flies.js";
import { readBwbSummary } from "./bwb.js";
import { readEarningsSummary } from "./earnings.js";
import { readCurveSummary } from "./curve.js";
import { readContangoSummary } from "./contango.js";
import { readCalendarsSummary } from "./calendars.js";
import { readPmccSummary } from "./pmcc.js";

/** Each module's simplified-view summary reader, by module id (`GET /api/summary/:module`).
 *  MEIC is absent on purpose: it keeps its own richer slide (`/api/meic/summary`). */
export const SUMMARY_READERS: Readonly<Record<string, SummaryReader>> = {
  flies: readFliesSummary,
  bwb: readBwbSummary,
  earnings: readEarningsSummary,
  curve: readCurveSummary,
  contango: readContangoSummary,
  calendars: readCalendarsSummary,
  pmcc: readPmccSummary,
};
