import type { ExternalCell, ExternalCellObject, ExternalColumn, ExternalFormat, ExternalRow, ExternalTone } from "@console/shared";

/**
 * How an external report's cells read: the one home for a column's format, its alignment and a cell's
 * tone. A format is a pure function of the number, checked against `formats` in
 * `packages/core/tests/fixtures/external_report_cases.json`, which the Python module
 * (`cherrypick.core.external_report.format_value`, what the Discord card prints) is checked against
 * too, so a number reads the same in the console and in a post.
 */

const MINUS = "−";
const DASH = "—";

export interface ColumnSpec {
  label: string;
  align: "left" | "right";
  format: ExternalFormat;
  tone: "none" | "sign";
  width: "narrow" | "wide" | null;
  stack: string[] | null;
}

export function columnSpec(column: ExternalColumn): ColumnSpec {
  const o = typeof column === "string" ? { label: column } : column;
  const stack = o.stack === undefined ? null : o.stack.map((s) => s.label);
  const format = (o as { format?: ExternalFormat }).format ?? "text";
  const align = (o as { align?: "left" | "right" }).align ?? (format === "text" && stack === null ? "left" : "right");
  return {
    label: o.label,
    align,
    format,
    tone: (o as { tone?: "none" | "sign" }).tone ?? "none",
    width: (o as { width?: "narrow" | "wide" }).width ?? null,
    stack,
  };
}

export interface CellParts {
  value: string | number | null;
  text: string | null;
  tone: ExternalTone | null;
  suffix: { text: string; tone: ExternalTone | null } | null;
  marker: "square" | "dot" | null;
}

/** A scalar or object cell as its parts. A stacked cell (an array) is read by the stack, not here. */
export function cellParts(cell: ExternalCell): CellParts {
  if (typeof cell === "object" && cell !== null && !Array.isArray(cell)) {
    const o = cell as ExternalCellObject;
    return {
      value: o.value ?? null,
      text: o.text ?? null,
      tone: o.tone ?? null,
      suffix: o.suffix === undefined ? null : { text: o.suffix.text, tone: o.suffix.tone ?? null },
      marker: o.marker ?? null,
    };
  }
  return { value: Array.isArray(cell) ? null : cell, text: null, tone: null, suffix: null, marker: null };
}

export const rowCells = (row: ExternalRow): ExternalCell[] => (Array.isArray(row) ? row : row.cells);

/** `value` grouped and rounded half up, as the Python module does: `toFixed` rounds the exact value. */
function fixed(value: number, places: number): string {
  const [whole = "0", frac] = value.toFixed(places).split(".");
  const grouped = whole.replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  return frac === undefined ? grouped : `${grouped}.${frac}`;
}

const trim = (s: string): string => (s.includes(".") ? s.replace(/0+$/, "").replace(/\.$/, "") : s);

/** `value` as the column's `fmt` shows it: a dash for null, a string as written. Money is whole
 *  dollars; percent takes the percent number itself (56 is 56%). */
export function formatValue(fmt: ExternalFormat, value: string | number | null): string {
  if (value === null) return DASH;
  if (typeof value !== "number") return value;
  const mag = Math.abs(value);
  const sign = value < 0 ? MINUS : "";
  switch (fmt) {
    case "money": {
      const body =
        mag >= 1e9 ? `${fixed(mag / 1e9, 2)}B`
        : mag >= 1e6 ? `${fixed(mag / 1e6, 2)}M`
        : mag >= 1e5 ? `${fixed(mag / 1e3, 0)}K`
        : mag >= 1e3 ? `${fixed(mag / 1e3, 1)}K`
        : fixed(mag, 2);
      return `${sign}$${body}`;
    }
    case "compact":
      if (mag >= 1e9) return `${sign}${fixed(mag / 1e9, 1)}B`;
      if (mag >= 1e6) return `${sign}${fixed(mag / 1e6, 1)}M`;
      if (mag >= 1e3) return `${sign}${fixed(mag / 1e3, 1)}K`;
      return `${sign}${fixed(mag, 0)}`;
    case "int":
      return `${sign}${fixed(mag, 0)}`;
    case "percent":
      return `${sign}${trim(fixed(mag, 1))}%`;
    case "signed": {
      const rounded = fixed(mag, 0);
      return rounded === "0" ? "0" : `${value > 0 ? "+" : MINUS}${rounded}`;
    }
    case "decimal":
      return `${sign}${trim(fixed(mag, 6))}`;
    default:
      return String(value);
  }
}

/** What a cell reads as: its `text` if it has one, else its value formatted by the column. */
export function cellText(col: ColumnSpec, parts: CellParts): string {
  return parts.text ?? formatValue(col.format, parts.value);
}

/** The cell's tone: its own, else the column's sign tone for a number, else none. */
export function cellTone(col: ColumnSpec, parts: CellParts): ExternalTone | null {
  if (parts.tone !== null) return parts.tone;
  if (col.tone === "sign" && typeof parts.value === "number" && parts.value !== 0) {
    return parts.value > 0 ? "pos" : "neg";
  }
  return null;
}
