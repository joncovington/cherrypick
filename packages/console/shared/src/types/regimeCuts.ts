/**
 * The regime-cuts slide (2026-09-19): per arm x per regime dimension x bucket outcomes, written
 * nightly by the module that owns the rows (`data/<module>/regime_cuts.json`, contract in
 * `cherrypick.core.regimecuts`) and rendered here WITHOUT recomputation.
 *
 * Three facts the payload keeps apart, because a reader would act on them differently: the file
 * is absent (the nightly job has not run on this machine), the file is malformed or of a version
 * this console does not read (`failed`), and the file is older than the ledger (`stale`, carried
 * on an otherwise good read). `thin` is the writer's flag, passed through and never derived here.
 */

export type RegimeCutsModule = "flies" | "meic";

/**
 * How much a cell's net rests on any one session (2026-09-28), stamped by the writer from the
 * cell's per-session totals. null on a thin cell (nothing more to read) and on any artifact written
 * before 2026-09-28. `netInterval` is a seeded session-level bootstrap of the cell's TOTAL net at
 * `intervalLevel`; `largestSessionShare` is that session's share of the cell's absolute flow.
 */
export interface RegimeRobustness {
  positiveSessions: number | null;
  largestSessionNet: number | null;
  largestSessionShare: number | null;
  signFlipsDroppingOne: number | null;
  netInterval: [number, number] | null;
  intervalLevel: number | null;
  intervalExcludesZero: boolean | null;
}

/**
 * How the cell's net read over the prior nightly snapshots (2026-09-28), stamped by the writer.
 * null on a thin cell and on any artifact written before 2026-09-28. `firstNet` is null when there
 * were no prior snapshots; `signChanges` counts zero-crossings along prior..current.
 */
export interface RegimeCellHistory {
  snapshots: number;
  firstNet: number | null;
  signChanges: number;
}

/**
 * Two buckets compared on the SAME sessions, per trade, with an exact two-sided sign test
 * (2026-09-28) -- what separates "that kind of day was good" from "entering in that regime was
 * good". Writer-stamped; the dimension's list is [] on artifacts written before 2026-09-28.
 */
export interface RegimePair {
  a: string;
  b: string;
  sessions: number;
  aBetterSessions: number;
  bBetterSessions: number;
  meanDiffPerTrade: number | null;
  medianDiffPerTrade: number | null;
  signTestP: number | null;
}

/**
 * How much of what clears the bar would clear it by chance (2026-09-28), stamped by the writer
 * over the whole document. null on artifacts written before 2026-09-28.
 */
export interface RegimeMultiplicity {
  /** The interval bar (1 - interval level). */
  alpha: number | null;
  /** The paired sign-test bar, published separately; null on the first 09-28 artifacts, which
   *  carried only `alpha` -- equal to it then, and read in its place. */
  pairedAlpha: number | null;
  intervals: number;
  intervalsExcludingZero: number;
  intervalsExpectedByChance: number | null;
  pairedTests: number;
  pairedBelowAlpha: number;
  pairedExpectedByChance: number | null;
}

export interface RegimeCell {
  bucket: string;
  valueMin: number | null;
  valueMax: number | null;
  sessions: number;
  trades: number;
  netPnl: number | null;
  avgPnl: number | null;
  winRate: number | null;
  profitFactor: number | null;
  /** null for a module without the concept (MEIC condors resolve; they do not complete). */
  completed: number | null;
  completionRate: number | null;
  /** Fewer than `thinBelowSessions` sessions -- stamped by the writer. */
  thin: boolean;
  /** One session carries >= 40% of the cell's absolute flow, or dropping any one flips the sign
   *  of its net -- stamped by the writer. null when thin, and on artifacts written before 2026-09-28. */
  fragile: boolean | null;
  robustness: RegimeRobustness | null;
  history: RegimeCellHistory | null;
}

export interface RegimeDimension {
  coveragePct: number | null;
  tagged: number;
  untagged: number;
  sessions: number;
  effectiveN: number;
  dailyScale: boolean;
  degenerate: boolean;
  underpowered: boolean;
  buckets: RegimeCell[];
  /** Same-day bucket comparisons, in the writer's order -- [] on artifacts written before 2026-09-28. */
  paired: RegimePair[];
}

export interface RegimeBreak {
  breakDate: string;
  scope: string;
  kind: string;
  reason: string | null;
}

export interface RegimeArm {
  arm: string;
  eraStart: string | null;
  eraBreak: RegimeBreak | null;
  sessions: number;
  trades: number;
  netPnl: number | null;
  winRate: number | null;
  completed: number | null;
  completionRate: number | null;
  /** Fewer than `thinBelowSessions` sessions -- stamped by the writer (2026-09-24). null on an
   *  artifact written before arm-level stamping: shown undimmed rather than re-derived here, and
   *  the module rewrites the file nightly. */
  thin: boolean | null;
  dimensions: Record<string, RegimeDimension>;
}

export interface RegimeCrossCell {
  buckets: string[];
  sessions: number;
  trades: number;
  netPnl: number | null;
  avgPnl: number | null;
  winRate: number | null;
  completed: number | null;
  completionRate: number | null;
  thin: boolean;
  /** One session carries >= 40% of the cell's absolute flow, or dropping any one flips the sign
   *  of its net -- stamped by the writer. null when thin, and on artifacts written before 2026-09-28. */
  fragile: boolean | null;
  robustness: RegimeRobustness | null;
  history: RegimeCellHistory | null;
}

export interface RegimeCrossTab {
  dims: string[];
  arms: Array<{ arm: string; cells: RegimeCrossCell[] }>;
}

export interface RegimeEra {
  start: string | null;
  /** MEIC also filters on its `era` column; the key it used, so a divergence from `start` is visible. */
  key: string | null;
  boundingBreak: RegimeBreak | null;
  breaks: Array<RegimeBreak & { binding: boolean }>;
  ignoredFuture: RegimeBreak[];
  caveats: RegimeBreak[];
}

export interface RegimeCuts {
  cutVersion: number;
  module: string;
  generatedAt: string | null;
  session: string | null;
  symbol: string | null;
  armColumn: string | null;
  entryModes: string[] | null;
  /** The writer's declared thresholds; null when the artifact does not publish one -- never a
   *  console copy of core's constant. */
  thinBelowSessions: number | null;
  minEffectiveN: number | null;
  era: RegimeEra;
  arms: RegimeArm[];
  crossTabs: RegimeCrossTab[];
  /** Union of dimension keys across arms, first-seen order -- the only thing this console derives. */
  dimensions: string[];
  /** Writer-stamped document-level chance baseline; null before 2026-09-28. */
  multiplicity: RegimeMultiplicity | null;
  /** The prior snapshots the cells' `history` was read over (oldest first); null when the writer
   *  did not stamp history (a stdout re-cut, or an artifact written before 2026-09-28). */
  history: { window: number | null; sessions: string[] } | null;
}

export type RegimeCutsPayload =
  | {
      status: "ok";
      cuts: RegimeCuts;
      /** Set when the ledger holds a session newer than the artifact's; null when it cannot be told. */
      stale: { artifactSession: string; latestSession: string } | null;
      /** Every dated artifact on disk, oldest first, for the session picker. */
      sessions: string[];
    }
  | { status: "absent"; path: string; sessions: string[] }
  | { status: "failed"; error: string; sessions: string[] };
