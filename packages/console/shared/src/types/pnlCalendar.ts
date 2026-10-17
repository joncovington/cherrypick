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

/**
 * One closed trade behind a calendar cell (`python -m cherrypick.core.metrics trades`), its fields
 * as core.ledgers names them. A field the schema does not record is null (n/r), never zero.
 * `slippage_basis` says which model the slippage is: `in_gross` (already conceded in the fill, a
 * measure only) or `cost` (charged, subtracted once). The rows add up: gross − fees − settlement
 * (− slippage where it is a cost) = net.
 */
export interface PnlTrade {
  tag: string;
  /** The module's own position key (earnings' order id); null where the schema keys none (MEIC). */
  position_id: string | null;
  symbol: string | null;
  strategy: string | null;
  session: string;
  entry_time: string | null;
  exit_time: string | null;
  entry_spot: number | null;
  exit_spot: number | null;
  gross_pnl: number | null;
  fees: number | null;
  settlement: number | null;
  slippage: number | null;
  slippage_basis: string | null;
  net_pnl: number | null;
}

/**
 * One position's excursion (`python -m cherrypick.core.excursions read`): its mark path from entry
 * to exit and the best and worst point on it, in the `basis` its module records:
 * `gross_at_mid` (built from legs; `reconciles` says whether the endpoint equals the recorded
 * gross), `gross_as_recorded` (earnings) or `net_of_fees_as_recorded` (flies live, marks before
 * settlement). `recorded: false` carries the reason a module keeps no path; never a guess.
 */
export interface PnlExcursion {
  ok: boolean;
  error?: string | null;
  recorded?: boolean;
  reason?: string;
  basis?: "gross_at_mid" | "gross_as_recorded" | "net_of_fees_as_recorded";
  ticks?: number;
  refused_ticks?: number;
  best?: { pnl: number; at: string } | null;
  worst?: { pnl: number; at: string } | null;
  /** [ISO time, pnl], thinned to a few hundred points with the ends and extremes kept. */
  path?: Array<[string, number]>;
  endpoint?: number | null;
  gross?: number | null;
  reconciles?: boolean | null;
}

/** One run of identical consecutive rows in a trace: how many, first and last time, the last detail. */
export interface PnlTraceRun {
  count: number;
  first: string | null;
  last: string | null;
  outcome?: string | null;
  block_detail?: string | null;
  action?: string;
  reason?: string | null;
  executed?: boolean;
  gate?: string | null;
  detail?: unknown;
}

/** A part of a trace a module may not record: `recorded: false` carries the reason. */
interface TracePart {
  recorded: boolean;
  reason?: string;
}

/**
 * One position's trace (`python -m cherrypick.core.trace read`): how it got in and what was decided
 * while it was held, read from what the module records. `entry.basis` says how the filled attempt
 * was found -- `linked` (the module ties it to the position), `matched` (session, arm, symbol and
 * time; checked to carry the position's strikes) or `review` (earnings' screening review).
 */
export interface PnlTrace {
  ok: boolean;
  error?: string | null;
  found?: boolean;
  reason?: string;
  entry?: TracePart & {
    basis?: "linked" | "matched" | "review";
    session?: string;
    attempts_before?: number;
    runs?: PnlTraceRun[];
    runs_omitted?: number;
    filled?: { at: string | null; detail: Record<string, unknown> };
    review?: Record<string, unknown>;
    criteria?: unknown;
  };
  orders?: TracePart & { orders?: Array<Record<string, unknown> & { at: string | null }> };
  management?: TracePart & {
    events?: number;
    runs?: PnlTraceRun[];
    runs_omitted?: number;
    executed?: Array<{ at: string | null; action: string; reason: string | null; detail: unknown }>;
  };
}

/** `GET /api/calendar/trades`: one arm's trades on one session. */
export interface PnlTradesResult {
  ok: boolean;
  module: PerformanceModuleId;
  session: string;
  tag: string;
  mode: TradingMode;
  trades: PnlTrade[];
  error: string | null;
}
