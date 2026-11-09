/**
 * External reports: JSON files a person's own scripts write to `data/external/<id>.json`
 * (`docs/guide/external-reports.md`). The contract is `external_report.schema.json` in
 * `packages/core/cherrypick/core/schemas/`; the server validates every file against that same file
 * and returns what drew and what did not.
 */
export type ExternalCell = string | number | null;

export type ExternalSection =
  | {
      heading: string;
      /** `bars`: indices of numeric columns drawn with an inline bar beside the number (optional). */
      table: { columns: string[]; rows: ExternalCell[][]; bars?: number[] };
    }
  | { heading: string; text: string };

/** A report that passed the contract. */
export interface ExternalReport {
  id: string;
  title: string;
  source: string;
  /** ISO 8601 with a UTC offset, as the script wrote it. */
  asOf: string;
  staleAfterHours: number;
  summary: string | null;
  sections: ExternalSection[];
  /** Hours between `asOf` and the read; null when the clock could not be read. */
  ageHours: number | null;
  /** True when `ageHours` is past `staleAfterHours`. */
  stale: boolean;
}

/** A file that did not: the file's name without `.json`, and every reason. Never half-drawn. */
export interface ExternalReportInvalid {
  id: string;
  reasons: string[];
}

export interface ExternalReportsPayload {
  reports: ExternalReport[];
  invalid: ExternalReportInvalid[];
  /** False when `data/external/` does not exist (nothing has ever been written there). */
  directoryExists: boolean;
}
