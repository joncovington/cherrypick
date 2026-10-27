/**
 * The research queue (`run.py queue-list`; packages/orchestrator `researchqueue.py`, 2026-10-24): long
 * research tasks the supervisor runs one at a time from rows. Read-only here: rows are added with
 * `run.py queue-add`, and every rule (what may run, when it may start, why a row waits) is the
 * orchestrator's. The console renames the reply and decides nothing.
 */

export type ResearchTaskStatus = "queued" | "running" | "done" | "failed" | "stopped" | "cancelled";

/** Where a finished row's product is (`researchqueue.result_of`). */
export interface ResearchTaskResult {
  log: string;
  /** A fetch's counts as its report gave them (complete, empty, refused, rows, seconds, stopped...). */
  report: Record<string, number | boolean> | null;
  /** A fetch: the request or plan file it worked through. */
  input: string | null;
  /** A fetch: the store it wrote to. */
  store: string | null;
  /** A script: the conclusion file it declared, and whether it exists. */
  output: string | null;
  outputExists: boolean | null;
}

export interface ResearchTask {
  id: number;
  /** The category: the study the row belongs to, a slug (item17, flow-tags). */
  study: string;
  /** What it does there, a slug (plan, fetch-chains, study). */
  step: string;
  /** `study/step`: the one display name. */
  name: string;
  /** The allow-listed kind: fetch-chains, fetch-flow or script. */
  kind: string;
  /** A script's declared conclusion file. */
  output: string | null;
  /** Set once the row finishes (or stops for the window). */
  result: ResearchTaskResult | null;
  /** The kind's arguments as queued (file paths, workers, a script's path and arguments). */
  args: Record<string, unknown>;
  /** "thetadata" for a task that needs the vendor session; "" for none. */
  needs: string;
  /** ET wall clock, YYYY-MM-DDTHH:MM(:SS); null when unset. */
  notBefore: string | null;
  stopBy: string | null;
  status: ResearchTaskStatus;
  createdAt: string;
  startedAt: string | null;
  finishedAt: string | null;
  /** How many times it has started (a fetch stopped for the nightly window resumes as a new run). */
  runs: number;
  exitCode: number | null;
  note: string | null;
  /** A queued row only: "ready", or why it waits now. */
  waits: string | null;
  /** What the task's own progress file says (`core.progress`), written by the job and its children;
   *  null for a task that has not written one. */
  progress: ResearchProgress | null;
}

export interface ResearchProgressPhase {
  name: string;
  done: number;
  total: number | null;
  unit: string;
  /** What the phase is working on now (a session date, a source). */
  current: string | null;
}

/** One job's progress, joined from its own file and its live children's (outermost phase first). */
export interface ResearchProgress {
  /** The file stem: `q<id>` for a queued task, `<job>-<pid>` for a run started by hand. */
  file: string;
  job: string;
  /** running / done / failed, or what the reader concluded: stalled (alive, silent 10 minutes) and
   *  orphaned (gone without a final state). */
  state: "running" | "done" | "failed" | "stalled" | "orphaned";
  phases: ResearchProgressPhase[];
  /** The overall share done across nested phases, 0-1; null when no phase has a total. */
  fraction: number | null;
  etaSeconds: number | null;
  /** Seconds since the job last wrote. */
  ageSeconds: number | null;
  /** The orchestrator's one-line reading, e.g. "decisions 1/3 replays (vendor) · sessions 2/4 days". */
  line: string;
  note: string | null;
  /** Epoch seconds. */
  startedAt: number | null;
  updatedAt: number | null;
}

export interface ResearchQueue {
  /** Whether a live process holds the ThetaData session lock right now. */
  sessionHeld: boolean;
  /** The nightly backtest-fetch window ("HH:MM" ET) no ThetaData task runs inside, on trading days. */
  blackout: [string, string] | null;
  tasks: ResearchTask[];
  /** Runs started by hand (no queue row), running or finished in the last 30 days. */
  byHand: ResearchProgress[];
}
