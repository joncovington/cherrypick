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
  thinBelowSessions: number;
  minEffectiveN: number;
  era: RegimeEra;
  arms: RegimeArm[];
  crossTabs: RegimeCrossTab[];
  /** Union of dimension keys across arms, first-seen order -- the only thing this console derives. */
  dimensions: string[];
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
