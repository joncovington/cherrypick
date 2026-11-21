// ------------------------------------------------------------------- changes
// Every modification under test in one module (`python -m cherrypick.core.changes`): advised twins
// against their base, arms against their control, and declared ideas and settings waiting on or past
// their one held-out test. Snake case as core prints it.

/** The modules `core.compare` reads, and so the modules with a changes list. */
export const CHANGES_MODULES = ["automations", "bwb", "calendars", "contango", "curve", "earnings", "flies", "ladder", "meic", "pmcc", "weeklyput"] as const;
export type ChangesModule = (typeof CHANGES_MODULES)[number];

export function isChangesModule(v: string): v is ChangesModule {
  return (CHANGES_MODULES as readonly string[]).includes(v);
}

export type ChangeReading = "ready" | "leaning" | "too_early" | "waiting" | "closed";

export interface ChangeGap {
  per_session: number | null;
  per_session_ci: [number, number] | null;
  per_trade: number | null;
  per_trade_ci: [number, number] | null;
  level: number | null;
  first: string | null;
  last: string | null;
}

export interface ChangeRow {
  /** `advised`: an advisor experiment's twin; `arm`: a configured variant beside its control;
   *  `declared`: a backtest idea or setting tested once on held-out sessions. */
  kind: "advised" | "arm" | "declared";
  /** The plain name: the experiment's, the arm's, or the declared row's title. */
  name: string;
  /** The experiment's hypothesis (first sentence) or the declared row's measure. */
  description: string | null;
  /** The experiment id, or the declared item's id. */
  id: string | null;
  arm: string;
  against: string;
  status: string | null;
  gap: ChangeGap | null;
  reading: ChangeReading;
  /** What the reading was read on: the per-trade pairing where there is one, else per session. */
  basis: "per_trade" | "per_session" | null;
  direction: "better" | "worse" | "even" | null;
  sessions: number | null;
  sessions_to_decision: number | null;
  source?: { kind: string | null; result: string | null; licensed: boolean };
  held_out?: { at: string | null; verdict: string | null; ci: [number, number] | null; use: number | null } | null;
}

/** `GET /api/changes/:module`. */
export interface ModuleChanges {
  module: string;
  status: string | null;
  reason: string | null;
  min_sessions: number | null;
  counts: Partial<Record<ChangeReading, number>>;
  rows: ChangeRow[];
  /** Declared items left out because their run is licensed and licensed results are hidden now. */
  withheld: number;
  error: string | null;
}
