/**
 * The studies board (`run.py hypotheses-board`; packages/orchestrator `hypotheses.board`, 2026-10-29,
 * docs/studies-board-plan.md): each row of the hypothesis register joined with its result files'
 * verdicts and its research-queue rows. The register decides the status; a verdict is the word the
 * study wrote, never worked out from figures. The console renames the reply and decides nothing.
 */

export type StudyGroup = "in_progress" | "declared" | "finished" | "observation";

export interface StudyVerdict {
  /** The arm the word is for; null when the file gives one word for the whole study. */
  arm: string | null;
  /** As written, lower case, `_` as a space: "fail", "pass", "no verdict". */
  verdict: string;
}

/** One tested setting of a grid study (packages/backtest `grid.block`). */
export interface StudyGridRow {
  arm: string;
  setting: number;
  /** As the study names it: "0.16", "14:00". */
  label: string;
  /** The arm's own mean net per session; null when the file has none. */
  mean: number | null;
  n: number | null;
  /** The arm's verdict word; null for a reference arm (the live setting, a reported one). */
  verdict: string | null;
  /** The live setting. */
  declared: boolean;
}

/**
 * A grid study's settings side by side, as the study wrote them. `neighbourhood` labels the live
 * setting and each passing one: "plateau", "lone_peak", "not_positive", "neighbour_better",
 * "no_neighbours" or "unmeasured". Description only; it never moves a verdict.
 */
export interface StudyGrid {
  parameter: string;
  measure: string;
  declared: string;
  rows: StudyGridRow[];
  neighbourhood: Record<string, string>;
}

export interface StudyResult {
  /** "register": a file the register lists. "queue": a conclusion a finished queue row declared that
   *  the register does not list yet (2026-11-02): the study's word, not yet recorded. */
  source: "register" | "queue";
  /** The file's name only; the full path stays on the Python side. */
  name: string;
  present: boolean;
  /** ET wall clock, as the orchestrator stamps. */
  modified: string | null;
  /** "design" / "holdout" when the file says so. */
  window: string | null;
  span: [string, string] | null;
  runId: string | null;
  provisional: boolean | null;
  /** Null: the file has no verdict word to read. */
  verdicts: StudyVerdict[] | null;
  /** The one word when every arm agrees, "mixed" when not, null when there is none. */
  summary: string | null;
  /** A grid study's settings side by side; null when the file has none. */
  grid: StudyGrid | null;
  /** The Markdown the study left beside the file (`results.md`), as written; null when none. */
  notes: string | null;
  /** Which version of its script's replay wrote the file; null when neither is known. */
  version: StudyVersion | null;
}

/** A correction to a study's replay, from the script's own log. */
export interface StudyCorrection {
  on: string;
  what: string;
  /** Made before any arm or outcome was read. */
  beforeRead: boolean;
}

export interface StudyVersion {
  /** The version the file was stamped with; null when it was written before stamping began. */
  version: number | null;
  /** The script's version now: the number of corrections in its log. */
  current: number | null;
  commit: string | null;
  /** A correction has landed since the file was written. */
  stale: boolean;
  /** The corrections the file predates, oldest first. */
  since: StudyCorrection[];
  /** The script's log could not be read. */
  error: string | null;
}

export interface StudyTask {
  id: number;
  step: string;
  status: string;
  createdAt: string | null;
  startedAt: string | null;
  finishedAt: string | null;
}

export interface StudyRival {
  explanation: string;
  check: string;
}

export interface StudyRow {
  id: number;
  /** The research queue's study slug: item23. */
  study: string;
  title: string;
  /** The register's: observation, declared, running, passed, failed, no_verdict, retired (closed unrun). */
  status: string;
  /** Items 1-17: their four parts live in the plan section, not the register. */
  legacy: boolean;
  /** `<file>#<heading>` in the repo. */
  pointer: string | null;
  /** The decision, in words. */
  outcome: string | null;
  measure: string | null;
  expected: string | null;
  sample: string | null;
  rule: string | null;
  mechanism: string | null;
  rivals: StudyRival[];
  group: StudyGroup;
  results: StudyResult[];
  queueActive: boolean;
  tasks: StudyTask[];
  lastActivity: string | null;
}

export interface StudiesBoard {
  rows: StudyRow[];
  /** `itemN` studies seen in the queue or as a result folder with no register row. */
  unregistered: string[];
}

/**
 * One arm of a run in brief, for a studies tile (`python -m cherrypick.backtest read summaries`,
 * 2026-11-03): the mean net a session, sessions and trades as the run's own report states them, and
 * its running net over the run thinned to at most 48 points, for the line's shape only.
 */
export interface StudyArmFigures {
  mean: number | null;
  sessions: number | null;
  trades: number | null;
  path: number[];
}

export interface StudyRunFigures {
  module: string | null;
  window: string | null;
  provisional: boolean;
  arms: Record<string, StudyArmFigures>;
}

/** The runs asked for, by id; `missing` lists the ids with no report on this machine. */
export interface StudyRunSummaries {
  runs: Record<string, StudyRunFigures>;
  missing: string[];
}

/** Run ids one summaries read answers for (the package's `SUMMARY_RUNS`). */
export const STUDY_SUMMARY_RUNS = 80;
