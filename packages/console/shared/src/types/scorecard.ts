/**
 * Compare's scorecard (`python -m cherrypick.cli scorecard`, the orchestrator's `scorecard.build`): one
 * row per arm and automation in the suite with each reading the suite already made, and the reason it
 * needs a look. Every reading and the look are Python's; the console renders them.
 */

export type ScorecardLook = "forward_kill" | "forward_drift" | "decision_due" | "losing" | "failed_running";

export interface ScorecardVsBacktest {
  id: number | null;
  study: string | null;
  /** collecting, confirm, drift, kill (or invalid); null while withheld. */
  state: string | null;
  /** Set against a licensed backtest reference while licensed results are hidden. */
  withheld: boolean;
  n: number | null;
  min_obs: number | null;
}

export interface ScorecardVsControl {
  against: string;
  /** core.changes' reading: ready, leaning, waiting, too_early, closed. */
  reading: string;
  direction: string | null;
  sessions: number;
  sessions_to_decision: number | null;
}

export interface ScorecardRow {
  module: string;
  arm: string;
  kind: "arm" | "automation";
  research: { id: number | null; verdict: string | null } | null;
  vs_backtest: ScorecardVsBacktest | null;
  vs_control: ScorecardVsControl | null;
  sample: { basis: "forward" | "vs_control" | "sessions"; have: number; need: number | null };
  sessions: number;
  trades: number;
  net: number | null;
  max_drawdown: number | null;
  running: boolean;
  /** The automations module's own state for an automation (on, waiting, off_group, off_own). */
  state?: string | null;
  look: ScorecardLook | null;
  look_words: string | null;
}

export interface Scorecard {
  rows: ScorecardRow[];
  eras: Record<string, { status: string | null; start: string | null }>;
  looks: Array<{ key: ScorecardLook; words: string }>;
  counts: Partial<Record<ScorecardLook, number>>;
  min_sessions: number | null;
  modules: string[];
  error: string | null;
}
