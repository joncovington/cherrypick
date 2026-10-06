/**
 * The flies intraday agent's page (`/flies/agent`, `GET /api/flies/agent`).
 *
 * Every verdict and every history figure here is the module's: the qualification file
 * (`data/flies/intraday_agent_qualification.json`, written by `cherrypick.flies.intraday_eval` at
 * each settlement and by `run.py agent-eval --write`) carries the criteria, the paired statistics,
 * each session's three arms, the settled tagged closes and the spend. The console adds only what
 * the file cannot hold yet: today's checks (the agent's own jsonl), the decision standing now, the
 * closes tagged on positions still open, and the gate's refusals today.
 */

export interface AgentArmDay {
  entries: number;
  stranded: number;
  tagged: number;
  settledNet: number;
  /** Net had every tagged close been taken at the natural price. */
  netCloses: number;
  /** The same at the 2x haircut: one more spread's worth on every close. */
  netCloses2x: number;
}

export interface AgentSessionRow {
  session: string;
  /** Whether the agent made at least one admissible paper decision that session. */
  decided: boolean;
  control: AgentArmDay | null;
  rule: AgentArmDay | null;
  agent: AgentArmDay | null;
  /** Agent less rule, settled net; null unless both arms traded that session. */
  agentLessRule: number | null;
}

export interface AgentCriterion {
  id: string;
  mode: "gates" | "gates_and_closures";
  label: string;
  value: number | null;
  threshold: number | null;
  /** null: the suite cannot score it yet. Null never unlocks. */
  pass: boolean | null;
}

export interface AgentPaired {
  n: number;
  mean: number | null;
  sd: number | null;
  lower95: number | null;
  sessionsNeeded: number | null;
}

export interface AgentTaggedClose {
  arm: string;
  positionId: string;
  tradeDate: string;
  side: string | null;
  center: number | null;
  wingWidth: number | null;
  quantity: number | null;
  /** Entry credit, per share. */
  credit: number | null;
  source: string | null;
  taggedAt: string | null;
  spot: number | null;
  /** Close debit at natural (pay the short's ask, take the long's bid), per share. */
  natural: number | null;
  mid: number | null;
  /** Modelled round-trip fees, recorded at the tag; null on a tag written before they were. */
  fees: number | null;
  settledNet: number;
  closedNet: number;
  closedNet2x: number;
  saved: number;
}

export interface AgentSpend {
  session: string;
  target: "paper" | "live";
  checks: number;
  calls: number;
  ok: number;
  costUsd: number;
  byModel: Record<string, { calls: number; costUsd: number }>;
}

/** One live session run in shadow mode, scored against paper (`intraday_eval.shadow_scoring`). */
export interface AgentShadowSession {
  session: string;
  /** Live entries stamped with the shadow. */
  entries: number;
  /** Those the agent's gate would have refused. */
  wouldRefuse: number;
  /** Minus the settled net of the would-refuse entries: what refusing them was worth on live. */
  liveEffect: number;
  /** Agent arm less control on paper, the same session; null without both. */
  paperEffect: number | null;
  /** Whether either effect moved: a session where neither did is no evidence. */
  counted: boolean;
  agree: boolean | null;
}

export interface AgentShadow {
  sessions: AgentShadowSession[];
  counted: number;
  agree: number;
  agreement: number | null;
  /** The shadow's named closes, tagged at live natural on the live rows (never executed). */
  closes: { tagged: number; saved: number | null; saved2x: number | null };
}

export interface AgentQualification {
  generatedAt: string | null;
  arms: { control: string; rule: string; agent: string; live: string | null };
  liveModeMax: string | null;
  /** The trend band the agent arm's gate resolves to, in points from the open. */
  trendBandPoints: number | null;
  sessions: { decided: number; paired: string[]; shadow: string[] };
  gates: AgentPaired & { targetEdge: number | null };
  closes: AgentPaired & { episodes: number; saved2x: number | null };
  strandRate: { rule: number | null; agent: number | null };
  shadow: AgentShadow;
  /** Whether the live loop can place a closing order; until it can, closures are never offered. */
  liveClosesBuilt: boolean | null;
  criteria: AgentCriterion[];
  unlocked: Record<string, boolean>;
  offeredModes: string[];
  perSession: AgentSessionRow[];
  taggedCloses: AgentTaggedClose[];
  spend: AgentSpend[];
}

/** One check the agent's job made: a model call, or a skip and why. */
export interface AgentCheck {
  /** New York wall clock, `YYYY-MM-DDTHH:MM:SS`, as every ledger here writes it. */
  at: string;
  target: string;
  called: boolean;
  skipped: string | null;
  trigger: string | null;
  model: string | null;
  costUsd: number | null;
  seconds: number | null;
  ok: boolean | null;
  error: string | null;
  trendGate: "on" | "off" | null;
  /** The labels the model named (p1, ...), as it saw them. */
  closeLabels: string[];
  droppedCloses: string[];
  confidence: number | null;
  reason: string | null;
  spot: number | null;
  /** Index into the session's record file, for `GET /api/flies/agent/pack`. */
  index: number;
}

export interface AgentDecisionNow {
  session: string | null;
  at: string | null;
  expiresAt: string | null;
  /** Unexpired and for the session in view: the arm is following it. */
  fresh: boolean;
  /** Seconds since the decision, at `now`. */
  ageSeconds: number | null;
  ok: boolean | null;
  trendGate: "on" | "off" | null;
  closeLabels: string[];
  reason: string | null;
  model: string | null;
  error: string | null;
}

export interface AgentOpenTag {
  positionId: string;
  arm: string;
  side: string | null;
  center: number | null;
  wingWidth: number | null;
  quantity: number | null;
  credit: number | null;
  source: string | null;
  taggedAt: string | null;
  spot: number | null;
  natural: number | null;
  mid: number | null;
}

export interface AgentGateRefusal {
  at: string;
  arm: string;
  center: number | null;
  spot: number | null;
}

export interface AgentConfigView {
  enabled: boolean;
  model: string | null;
  paper: boolean;
  paperArm: string | null;
  liveModeMax: string | null;
  triggerBandPoints: number | null;
  decisionTtlMinutes: number | null;
  maxCallsPerSession: number | null;
  minMinutesBetweenCalls: number | null;
}

export interface FliesAgentPayload {
  session: string | null;
  /** Sessions with a record file or a row in the qualification file, newest first. */
  sessions: string[];
  config: AgentConfigView | null;
  decision: AgentDecisionNow | null;
  checks: AgentCheck[];
  /** The session's paper checks summed: the page's tile, so no component adds money. */
  sessionSpend: { checks: number; calls: number; costUsd: number };
  /** The session's day open as the agent's pack saw it; null until a call has been made. */
  dayOpen: number | null;
  openTags: AgentOpenTag[];
  refusals: AgentGateRefusal[];
  qualification: AgentQualification | null;
  /** Why the qualification is null: absent (not yet written) or a read failure. */
  qualificationStatus: "ok" | "absent" | "failed";
  now: string;
}

export interface FliesAgentPack {
  session: string;
  index: number;
  /** The pack as the model saw it: every `_`-prefixed key removed (`intraday_pack.for_model`). */
  pack: unknown;
}
