// ------------------------------------------------------------------- the Live page's arm switches
// The one write the Live page has. A switch asks the module's OWN command to write (or delete) today's
// arm record (`services/liveArmingBridge.ts`); nothing here arms by itself, places an order or clears
// the halt flag. Shown only while `live_arming` is true in console.json.

/** The loops a person can arm from here. Every other live loop (flies and bwb are retired, a second
 *  login's MEIC account) has no working switch. */
export type ArmableLoop = "meic" | "ladder";

/** One line of what the loop will do once armed, in the module's own figures. */
export interface LiveArmFact {
  label: string;
  value: string;
}

/** A gate the module's readiness named (MEIC's blockers and warnings, the ladder's nine gates). */
export interface LiveArmGate {
  text: string;
  /** Said as a warning (a YES may proceed past it) rather than a blocker. */
  warning: boolean;
}

/**
 * What the dialog shows, read fresh from the module's `--status` when it opens. `dialogId` is the
 * server's one-use token for exactly this readout: present only when the loop is ready, unhalted and
 * not yet armed today, and the only way to a confirmed arm. `readoutHash` is not sent: the server holds it.
 */
export interface LiveArmReadout {
  ok: boolean;
  error: string | null;
  loop: ArmableLoop;
  /** The card's words: "meic · SPX" / "ladder". */
  title: string;
  session: string | null;
  ready: boolean;
  blockers: string[];
  warnings: string[];
  halted: boolean;
  armedToday: boolean;
  supervisorFresh: boolean | null;
  facts: LiveArmFact[];
  /** The ladder only: `dry-run` places and cancels nothing, `live` does. */
  mode: "dry-run" | "live" | null;
  modeNote: string | null;
  /** DISCLAIMER.md, read on the server, byte for byte. Null when the file is missing: no arm without it. */
  disclaimer: string | null;
  dialogId: string | null;
}

export type ArmAttemptState = "running" | "done" | "failed";

/** One arm, run by the server so the person may leave the page. */
export interface ArmAttempt {
  id: string;
  loop: ArmableLoop;
  session: string;
  startedAt: string;
  finishedAt: string | null;
  state: ArmAttemptState;
  /** `writing` the record; `waiting` for the supervisor to enable the job; `verifying`; `done`. */
  step: "writing" | "waiting" | "verifying" | "done";
  /** The arm record exists (the module's command answered ok). */
  recordWritten: boolean;
  /** MEIC ticks under the supervisor's `meic-live` job; the ladder has no job to wait for yet. */
  needsSupervisor: boolean;
  /** Armed already when asked: nothing was run. */
  noop: boolean;
  /** The one-line result, in words from the module or from a fresh status. */
  message: string;
  /** The module's blockers or warning, as it came back. */
  detail: string[];
  /** What a fresh `--status` said last: `armed_for` today, or not. */
  armedToday: boolean | null;
}

export interface LiveLoopSwitch {
  loop: string;
  /** `armable`: a working switch. `locked`: shown, cannot move (`lockedReason` says why). */
  state: "armable" | "locked";
  lockedReason: string | null;
}

/** `GET /api/live/arming`: whether the switches are shown at all, and the server's attempts. */
export interface LiveArmingState {
  enabled: boolean;
  switches: LiveLoopSwitch[];
  attempts: ArmAttempt[];
}

/** A refusal from the arm or disarm route: what stopped it, and the fresh readout when it changed. */
export interface LiveArmRefusal {
  error: string;
  code: string;
  blockers?: string[];
  readout?: LiveArmReadout;
}
