import type { ModuleSummarySlice } from "@console/shared";
import type { ConsoleConfig } from "../../config.js";
import { emptySlice, summaryWindow, type SummaryQuery } from "./common.js";

/** Contango's simplified-view summary. Not built yet: the slide says so rather than drawing zeros. */
export function readContangoSummary(_config: ConsoleConfig, q: SummaryQuery): ModuleSummarySlice {
  const slice = emptySlice("contango", q.mode, "one SVXY or SHV holding, switch to switch", summaryWindow(q.range, q.day));
  slice.notes.push("This module's summary is not built yet.");
  return slice;
}
