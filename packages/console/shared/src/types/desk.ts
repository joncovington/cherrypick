import type { TradingMode } from "./status.js";

/**
 * The Overview's per-producer liveness strip: age of the last event/iteration against the
 * producer's own declared cadence, so a stalled feed cannot look like a quiet market.
 *
 * `cadenceSeconds` is `null` when the producer's cadence could not be read (no config path wired
 * for this producer yet, or the field was absent) -- the row still renders its age, with no
 * over/under judgement, rather than inventing a threshold. This is the largest new reader in the
 * desk payload: the console did not read loop cadences suite-wide before it.
 */
export interface DeskLiveness {
  id: string;
  label: string;
  kind: "streamer" | "loop" | "recorder";
  ageSeconds: number | null;
  cadenceSeconds: number | null;
  /** `ageSeconds - cadenceSeconds` when positive and both are known; null otherwise. */
  overBy: number | null;
}

/** One module's open-book exposure, right now. `atRiskLabel` states its own basis (credit
 *  structures report max loss; debit structures report the debit paid) so the column never
 *  implies a comparability the numbers don't have. */
export interface DeskExposureRow {
  module: string;
  open: number | null;
  atRisk: number | null;
  atRiskLabel: string;
  unrealisedNet: number | null;
  markAgeSeconds: number | null;
  available: boolean;
  note: string | null;
}

export interface DeskEntriesRow {
  module: string;
  filled: number;
  refused: number;
  noFill: number;
  sessionNet: number | null;
  topRefusal: string | null;
  available: boolean;
  note: string | null;
}

export interface DeskEvidenceRow {
  module: string;
  lastBreakDate: string | null;
  lastBreakReason: string | null;
  /** Sessions since the last break, counted from the suite report's own per-module session
   *  series -- never a second calendar. Null when the module has no break on record (not zero:
   *  "no break" and "zero sessions since one" are different facts). */
  sessionsSince: number | null;
}

export interface DeskEodRow {
  module: string;
  net: number | null;
  closed: number | null;
  netPerTrade: number | null;
}

export interface DeskPayload {
  mode: TradingMode;
  liveness: DeskLiveness[];
  exposure: DeskExposureRow[];
  entries: DeskEntriesRow[];
  evidence: DeskEvidenceRow[];
  eod: { session: string | null; rows: DeskEodRow[] };
}
