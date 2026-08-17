// --------------------------------------------------------------------------- Morning report
// Shapes mirror `packages/overview`'s fact pack (data/overview/morning-<session>.json). The console
// renders that artifact and derives nothing from it — the phase, the gate verdicts and the
// strongest/weakest sectors are all precomputed by the pack's writer, and this page displays them.
//
// Null is never zero anywhere in here: an unmeasured reading renders as an em dash, because a VIX
// that was not captured and a VIX of 0 are different facts.

/** One market reading on the scorecard — value plus the provenance the page must show beside it. */
export interface MorningReading {
  value: number | null;
  /** "live" = captured pre-open this session; "prior" = the previous session's close stood in. */
  basis: string | null;
  /** The session the value actually belongs to — matters most when basis is "prior". */
  session: string | null;
  asOf: string | null;
  source: string | null;
  label: string | null;
  priorClose: number | null;
  priorChangePct: number | null;
}

export interface MorningLevels {
  symbol: string | null;
  referencePrice: number | null;
  referenceBasis: string | null;
  zeroGamma: number | null;
  callWall: number | null;
  putWall: number | null;
  netGex: number | null;
  session: string | null;
  asOf: string | null;
  source: string | null;
}

export interface MorningSectorRow {
  symbol: string;
  sector: string | null;
  changePct: number | null;
  close: number | null;
  session: string | null;
}

export interface MorningSectors {
  board: MorningSectorRow[];
  /** Precomputed by the pack — never re-derived from the board here. */
  strongest: MorningSectorRow | null;
  weakest: MorningSectorRow | null;
  measured: number | null;
}

export interface MorningGate {
  id: string;
  label: string;
  /** met | not_met | unknown — anything unfamiliar is read as unknown, never as met. */
  status: string;
  value: number | null;
  threshold: number | null;
  detail: string | null;
}

export interface MorningPhase {
  /** green | yellow | red, as the pack computed it. */
  phase: string;
  reason: string | null;
  gatesTotal: number | null;
  gatesMeasured: number | null;
  gatesMet: number | null;
}

/** One macro signal feeding the deployment score, already scored 0–100 by the pack. */
export interface MorningSignal {
  id: string;
  label: string;
  /** measured | unknown — anything unfamiliar reads as unknown, never as measured. */
  status: string;
  /** The signal's own 0–100 contribution, or null when it could not be measured. */
  score: number | null;
  /** The raw quantity behind the score (a ratio, a z, a percentage) — units vary by signal. */
  value: number | null;
  /** Declared blend weight as a fraction, e.g. 0.25. */
  weight: number | null;
  detail: string | null;
}

/**
 * The deployment score block. **Record-only** — it gates nothing, sizes nothing, and the page must
 * never present it as an instruction. The session phase beside it is the operative verdict, and the
 * two are free to disagree.
 */
export interface MorningDeployment {
  /** 0–100, or null when too few signals were measured to blend one honestly. */
  score: number | null;
  /** full | reduced | defensive, as the pack computed it; null when there is no score. */
  zone: string | null;
  signals: MorningSignal[];
  signalsMeasured: number | null;
  signalsTotal: number | null;
  /** True when the blend renormalized its weights over fewer than all signals. */
  weightsRenormalized: boolean | null;
  /** Signals declared but not yet built — shown so an absent input is visible, not invisible. */
  deferred: string[];
  /** Why there is no score, when there is none. */
  reason: string | null;
  /** The pack's own statement that this governs nothing. Rendered, never paraphrased. */
  note: string | null;
}

export interface MorningCalendar {
  isFomcDay: boolean | null;
  nextFomc: string | null;
  fomcYearKnown: boolean | null;
  isTripleWitching: boolean | null;
  isQuarterlyExpiry: boolean | null;
  nextTradingDay: string | null;
}

export interface MorningPack {
  session: string;
  factVersion: number | null;
  generatedAt: string | null;
  /** Keyed by the pack's own reading ids (spx, vix, vix3m, …) — unfamiliar keys pass through. */
  readings: Record<string, MorningReading>;
  levels: MorningLevels | null;
  sectors: MorningSectors | null;
  gates: MorningGate[];
  phase: MorningPhase | null;
  /** Absent on packs written before fact version 2 — the page renders nothing rather than zeros. */
  deployment: MorningDeployment | null;
  calendar: MorningCalendar | null;
}

export interface MorningPayload {
  sessions: string[];
  current: MorningPack | null;
  /** The AI-written narrative, if one has been written. Interpretation — never mixed into the facts. */
  note: string | null;
}
