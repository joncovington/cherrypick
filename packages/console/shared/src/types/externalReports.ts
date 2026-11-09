/**
 * External reports: JSON files a person's own scripts write to `data/external/<id>.json`
 * (`docs/guide/external-reports.md`). The contract is `external_report.schema.json` in
 * `packages/core/cherrypick/core/schemas/`; the server validates every file against that same file
 * and returns what drew and what did not.
 *
 * Presentation hints (formats, tones, stacked bars, keys, footnotes) are all optional: a column is a
 * string or an object, a cell a scalar or an object, a row an array or `{ cells, emphasis }`.
 */
export type ExternalScalar = string | number | null;
/** A stacked column's cell: one number per segment (null is a segment with nothing). */
export type ExternalStackCell = (number | null)[];

/** A cell's colour by meaning; each maps to a theme token, so it reads in light and dark. */
export type ExternalTone = "pos" | "neg" | "warn" | "muted" | "strong";

export interface ExternalCellObject {
  value?: ExternalScalar;
  /** Shown instead of the formatted value. */
  text?: string;
  tone?: ExternalTone;
  suffix?: { text: string; tone?: ExternalTone };
  marker?: "square" | "dot";
}

export type ExternalCell = ExternalScalar | ExternalCellObject | ExternalStackCell;

export type ExternalFormat = "text" | "money" | "compact" | "int" | "percent" | "signed" | "decimal";

export interface ExternalColumnObject {
  label: string;
  align?: "left" | "right";
  format?: ExternalFormat;
  tone?: "none" | "sign";
  width?: "narrow" | "wide";
  stack?: { label: string }[];
  /** A bar per number: a fixed `scale` [min, max], a grey `track`, and `own_column` for its own column. */
  bar?: { scale?: [number, number]; track?: boolean; own_column?: boolean };
}
export type ExternalColumn = string | ExternalColumnObject;

export type ExternalRow = ExternalCell[] | { cells: ExternalCell[]; emphasis?: "strong" | "dim" };

export interface ExternalKeyEntry {
  tag: string;
  tone?: ExternalTone;
  label?: string;
  text: string;
}

export type ExternalSection =
  | {
      heading: string;
      /** A leading # column counting the rows from 1. */
      numbered?: boolean;
      /** The legend under the table. */
      key?: ExternalKeyEntry[];
      /** The muted line at the card's foot. */
      footnote?: string;
      /** `bars`: indices of numeric columns drawn with an inline bar beside the number (optional). */
      table: { columns: ExternalColumn[]; rows: ExternalRow[]; bars?: number[] };
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
  /** Draw the source and as_of line under each section card (the report's `show_source_foot`, default true). */
  showSourceFoot: boolean;
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
