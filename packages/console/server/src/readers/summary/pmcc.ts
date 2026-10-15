import type { ModuleSummarySlice } from "@console/shared";
import type { ConsoleConfig } from "../../config.js";
import { emptySlice, summaryWindow, type SummaryQuery } from "./common.js";

/** Pmcc's simplified-view summary. Not built yet: the slide says so rather than drawing zeros. */
export function readPmccSummary(_config: ConsoleConfig, q: SummaryQuery): ModuleSummarySlice {
  const slice = emptySlice("pmcc", q.mode, "one short call sold against a long call", summaryWindow(q.range, q.day));
  slice.notes.push("This module's summary is not built yet.");
  return slice;
}
