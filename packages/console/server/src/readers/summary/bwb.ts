import type { ModuleSummarySlice } from "@console/shared";
import type { ConsoleConfig } from "../../config.js";
import { emptySlice, summaryWindow, type SummaryQuery } from "./common.js";

/** Bwb's simplified-view summary. Not built yet: the slide says so rather than drawing zeros. */
export function readBwbSummary(_config: ConsoleConfig, q: SummaryQuery): ModuleSummarySlice {
  const slice = emptySlice("bwb", q.mode, "a ~7 DTE SPX put broken-wing butterfly, held to expiry", summaryWindow(q.range, q.day));
  slice.notes.push("This module's summary is not built yet.");
  return slice;
}
