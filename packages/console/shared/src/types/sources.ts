/**
 * The source registry (`cherrypick.core.sources`, #1194): every data store the suite holds, ours and
 * the vendor's, with its state and span from the last inventory, and which source covers a request.
 * Read through `python -m cherrypick.core.sources list | coverage`. The shapes are core's own; nothing
 * here is recomputed.
 */

/** What a source can supply, in core's words (`sources.FIELDS`). */
export const SOURCE_FIELDS = [
  "closes",
  "ohlc",
  "volume",
  "spot",
  "chain",
  "greeks",
  "iv",
  "iv_history",
  "oi",
  "profile",
  "rates",
] as const;
export type SourceField = (typeof SOURCE_FIELDS)[number];

/** Cadences finest first (`sources.CADENCE_SECONDS`). */
export const SOURCE_CADENCES = ["~10s", "~15s", "1m", "5m", "1h", "1d"] as const;
export type SourceCadence = (typeof SOURCE_CADENCES)[number];

/** The expiries a chain source holds (`sources.EXPIRIES`). */
export const SOURCE_EXPIRIES = ["0dte", "1dte", "weekly", "monthly", "all"] as const;
export type SourceExpiry = (typeof SOURCE_EXPIRIES)[number];

/** `read`: inventoried. `absent`: not on this machine. `skipped`: not read on the last run (Dolt).
 *  `error`: could not be read. `not read`: no inventory has reached it yet. */
export type SourceState = "read" | "absent" | "skipped" | "error" | "not read";

export interface SourceRow {
  name: string;
  holds: string;
  kind: string;
  fields: string[];
  cadence: string;
  expiries: string[];
  licensed: boolean;
  writer: string;
  ongoing: boolean;
  rolling: boolean;
  note: string | null;
  state: SourceState;
  reason: string | null;
  symbols: number | null;
  first: string | null;
  last: string | null;
  read_at: string | null;
}

/** `GET /api/sources`. */
export interface SourcesSummary {
  generated_at: string | null;
  sources: SourceRow[];
  /** Data files under the data root no source declares. */
  undeclared: string[];
  /** Licensed (vendor) sources the server left out because their results may not be shown. */
  withheld: number;
}

export interface CoverageCandidate {
  source: string;
  cadence: string;
  licensed: boolean;
  rolling: boolean;
  covers: boolean;
  why: string | null;
  first?: string;
  last?: string;
  sessions?: number | null;
  density?: number | null;
}

/** `GET /api/sources/coverage`. */
export interface SourceCoverage {
  field: string;
  symbol: string;
  start: string;
  end: string;
  cadence: string | null;
  expiry: string | null;
  covered: boolean;
  best: string | null;
  sources: CoverageCandidate[];
}

/** A symbol as the coverage route accepts it: letters, digits and `/ . -`, as `/ES`, `BRK.B`. */
export const SOURCE_SYMBOL = /^[A-Z0-9/.-]{1,16}$/;
