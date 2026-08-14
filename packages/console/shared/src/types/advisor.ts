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
 * What actually reached the loops for the next session, per module. Read from the artifact plus
 * each module's own `advice_active.json`, so the page can say "written" and "the loop applied it"
 * as the two separate facts they are.
 */
export interface AdvisorApplyStatus {
  module: string;
  nextSession: string | null;
  artifactWritten: boolean;
  artifactProposals: Array<{ param: string; value: unknown; rationale: string }>;
  artifactRejected: Array<{ param: string | null; value: unknown; reason: string }>;
  /** The module's frozen read-once decision for the session it names, verbatim. */
  consumerDecision: Record<string, unknown> | null;
  /** Why the module is not accepting advice, when it is not. */
  disabledReason: string | null;
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
