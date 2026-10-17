// ------------------------------------------------------------------- the P&L calendar (shared)
// Compare's calendar tab (`GET /api/calendar`): every arm's net per session, from the per-session
// nets `core.metrics` already computes for each module's performance tab (`session_nets_dated`),
// over the suite window. docs/strategy-analysis-plan.md Phase 4.

import type { PerformanceModuleId } from "./performance.js";
import type { TradingMode } from "./status.js";

/** One arm's settled net on one session. `tag` is the metrics group: `<arm>` or `<arm>@<experiment>`. */
export interface PnlDay {
  module: PerformanceModuleId;
  tag: string;
  session: string;
  net: number;
}

export interface PnlCalendarResult {
  mode: TradingMode;
  /** The suite window's start; the calendar shows nothing before it. */
  from: string | null;
  note: string | null;
  days: PnlDay[];
  /** A module whose read failed: named, never an empty month. */
  failures: Array<{ module: PerformanceModuleId; error: string }>;
}
