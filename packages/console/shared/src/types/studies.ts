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

export interface StudyResult {
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
  /** The register's: observation, declared, running, passed, failed, no_verdict. */
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
