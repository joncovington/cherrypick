// --------------------------------------------------------------------------- AI advisor
// Shapes mirror `packages/advisor`'s store (data/advisor/advisor.db) and the advice artifacts it
// issues (state/advice/<module>-<session>.json). The console renders that record and computes
// nothing of its own about it — the advisor's verdicts are computed in Python, by the same chain
// (ledger readers → compare_profiles → qualify_readings) every other promotion decision uses.
//
// Two things this page has to keep visible that a naive rendering would drop: a REJECTED proposal
// and why (a rejection nobody sees gets re-proposed forever), and whether a reading is
// UNDERPOWERED (not measured is a third state, distinct from passed and failed).

export interface AdvisorFlag {
  module: string;
  severity: string;
  text: string;
}

export interface AdvisorCheckpoint {
  session: string;
  slot: string;
  /** The alias the slot was configured with (e.g. "opus"), which floats by design. */
  model: string | null;
  /** The exact model id the CLI resolved that alias to, recorded since 2026-09-12; null before. */
  modelId: string | null;
  ok: boolean;
  error: string | null;
  observations: string[];
  flags: AdvisorFlag[];
  createdAt: string | null;
}

export interface AdvisorProposal {
  id: number;
  session: string | null;
  slot: string | null;
  module: string | null;
  kind: string;
  /** proposed | admitted | rejected | superseded | dismissed */
  status: string;
  rejectReason: string | null;
  experimentId: string | null;
  /** The model's own proposal object, verbatim. Rendered per kind; never re-derived. */
  payload: Record<string, unknown>;
  createdAt: string | null;
}

/** One arm's reading beside its control, as the verdict computed it. */
export interface AdvisorPair {
  advisedTag: string;
  baseTag: string;
  advised: Record<string, unknown> | null;
  base: Record<string, unknown> | null;
  delta: Record<string, number | null>;
  /** `{tag: {qualified, checks}}` from qualify_readings — the promotion gate, unmodified. */
  qualification: Record<string, unknown>;
  underpowered: boolean;
}

export interface AdvisorVerdict {
  pairs: AdvisorPair[];
  underpowered: boolean;
  /** The model's keep/kill/promote, stored beside the numbers — never instead of them. */
  recommendation: { value: string; rationale: string; by: string; session: string } | null;
  /**
   * Set when the experiment was concluded by the calendar exit (2026-09-12): it had not run its
   * course after twice its length in calendar sessions, because its module's loop stopped
   * recording decisions. `sessionsRun` is how many sessions it actually got.
   */
  stalled: { sessionsRun: number; calendarSessions: number } | null;
}

export interface AdvisorEvent {
  session: string | null;
  event: string;
  detail: Record<string, unknown> | null;
  createdAt: string | null;
}

export interface AdvisorExperiment {
  id: string;
  module: string;
  /** The book this experiment's advised twin is measured against. */
  baseProfile: string;
  name: string | null;
  /** The book this experiment writes, `advised:<name>` (2026-09-17): the row's own `tag` column,
   *  or derived from the name on a store that predates the column; null for a legacy row with
   *  neither, whose book was `advised:<base>`. */
  tag: string | null;
  hypothesis: string | null;
  successMetric: string | null;
  params: Record<string, unknown>;
  /** queued | active | expired | killed */
  status: string;
  createdSession: string;
  sessionsRun: number;
  expiresAfter: number;
  verdict: AdvisorVerdict | null;
  journal: AdvisorEvent[];
}

/**
 * The advisor's reconciliation of one session: was the artifact issued for it applied by the loop?
 *
 * Computed and stored by `packages/advisor`'s `enactment.py`, never re-derived here. Comparing an
 * artifact's params to a loop's recorded decision is a judgement, and a second opinion in
 * TypeScript is free to drift from the first — the same reason verdicts live on the experiment row.
 */
export interface AdvisorEnactment {
  session: string;
  /** enacted | carried | not_enacted | no_artifact */
  status: string;
  /** Why, in the advisor's own words — the text to show when it did not reach the loop. */
  detail: string | null;
  experimentId: string | null;
  decisionReason: string | null;
  scoredAt: string | null;
}

/** One experiment's entry in an advice artifact or a module's frozen decision file -- since
 *  2026-09-17 both carry an `experiments` list, one entry per concurrent experiment, and the
 *  reader synthesises a single entry from the legacy top-level fields when the list is absent. */
export interface AdvisorArtifactExperiment {
  experimentId: string | null;
  name: string | null;
  tag: string | null;
  base: string | null;
  proposals: Array<{ param: string; value: unknown; rationale: string }>;
  rejected: Array<{ param: string | null; value: unknown; reason: string }>;
}

export interface AdvisorDecisionExperiment {
  experimentId: string | null;
  name: string | null;
  tag: string | null;
  base: string | null;
  params: Record<string, unknown> | null;
  reason: string | null;
}

/**
 * What actually reached the loops, per module. Three separate facts, kept separate because they
 * come apart in ordinary operation and collapsing them is what hid the 2026-08-25 incident: the
 * advisor WROTE an artifact, the loop APPLIED it, and something is queued for tomorrow.
 *
 * Until then this table showed tomorrow's artifact beside today's decision — two different
 * sessions, which can never agree — so "written ✓" sat next to "advice_disabled" for two modules
 * that had dropped their artifact, and neither the row nor the collapsed card said anything was
 * wrong.
 */
export interface AdvisorApplyStatus {
  module: string;
  /** The session the QUEUED artifact is for — tomorrow, when the evening pass has run. */
  nextSession: string | null;
  artifactWritten: boolean;
  /** Mirror of the first experiment's admitted/rejected lists, the pre-2026-09-17 shape. */
  artifactProposals: Array<{ param: string; value: unknown; rationale: string }>;
  artifactRejected: Array<{ param: string | null; value: unknown; reason: string }>;
  /** One entry per experiment the artifact carries (a single synthesised entry for an artifact
   *  written before the list existed). Empty when no artifact is written. */
  artifactExperiments: AdvisorArtifactExperiment[];
  /** The module's frozen read-once decision for the session it names, verbatim. */
  consumerDecision: Record<string, unknown> | null;
  /** The decision's `experiments` list, or its legacy `params`/`experiment_id` as one entry. */
  decisionExperiments: AdvisorDecisionExperiment[];
  /** Why the module is not accepting advice, when it is not. */
  disabledReason: string | null;
  /** Whether the artifact issued for the CHOSEN session reached this module's loop -- one row per
   *  experiment since the advisor's `enactment` table keyed on (session, module, experiment_id)
   *  (2026-09-17); a row with a null experiment id is the module's own "nothing issued". Empty
   *  when the advisor has not scored the session. */
  enactments: AdvisorEnactment[];
}

/** One scored session for a module, oldest first in `AdvisorModulePayload.sessions`. */
export interface AdvisorSessionCell {
  session: string;
  /** enacted | carried | not_enacted | no_artifact */
  status: string;
  experimentId: string | null;
  detail: string | null;
}

/**
 * One module's view of the advisor: the experiment running on it, how far it has got, what
 * happened session by session, what is queued behind it, and what tomorrow's artifact says.
 * Read-only, module-scoped, and rendered as an "advisor" slide inside the module's own lightbox
 * (2026-09-12) — a reader looking at a module asks "is my A/B working", which the cross-module
 * advisor page did not answer at a glance.
 */
/** An active experiment with its own progress against the advisor's calendar exit. */
export interface AdvisorActiveExperiment extends AdvisorExperiment {
  /** Sessions the advisor has scored for this module since this experiment was created — its
   *  calendar age as the advisor counts it. Null on a store without the enactment table. */
  calendarSessions: number | null;
  /** The calendar exit fires past this many sessions: twice the experiment's length. */
  stallBudget: number;
}

export interface AdvisorModulePayload {
  module: string;
  storePresent: boolean;
  /** Every experiment running on this module, in activation order — several at once since
   *  2026-09-17, each on its own advised book. */
  active: AdvisorActiveExperiment[];
  queued: AdvisorExperiment[];
  /** The most recently concluded experiments on this module, newest first. */
  concluded: AdvisorExperiment[];
  /** Every enactment row over the last scored sessions for this module, oldest first — one per
   *  experiment per session, so the strip is drawn per experiment (`experimentId`). */
  sessions: AdvisorSessionCell[];
  /** Tomorrow's artifact and whether the module accepts advice — the same apply status the
   *  advisor page shows, for this module alone. */
  tomorrow: AdvisorApplyStatus | null;
}

export interface AdvisorPayload {
  /** Sessions with at least one checkpoint, oldest first. */
  sessions: string[];
  session: string | null;
  /** Today's checkpoints, one per slot, in slot order. */
  latest: AdvisorCheckpoint[];
  /** Recent checkpoint history for the ok-rate table, newest first. */
  checkpoints: AdvisorCheckpoint[];
  proposals: AdvisorProposal[];
  experiments: AdvisorExperiment[];
  applyStatus: AdvisorApplyStatus[];
  /** False before the advisor has ever run: the page renders empty rather than erroring. */
  storePresent: boolean;
}
