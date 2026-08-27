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
  model: string | null;
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
  baseProfile: string;
  name: string | null;
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
  artifactProposals: Array<{ param: string; value: unknown; rationale: string }>;
  artifactRejected: Array<{ param: string | null; value: unknown; reason: string }>;
  /** The module's frozen read-once decision for the session it names, verbatim. */
  consumerDecision: Record<string, unknown> | null;
  /** Why the module is not accepting advice, when it is not. */
  disabledReason: string | null;
  /** Whether the artifact issued for the CHOSEN session reached this module's loop. */
  enactment: AdvisorEnactment | null;
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
