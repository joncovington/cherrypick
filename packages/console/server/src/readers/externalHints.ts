/**
 * The checks on an external report's presentation hints that the schema cannot say: a row is as wide
 * as its columns, a stacked column's cells hold one number per segment, a bar column is numeric, a
 * sign tone sits on a numeric format, a percent column holds percents. Mirrors `_table_problems` in
 * `cherrypick.core.external_report`; both read `external_report_cases.json`, so they cannot drift.
 */
import type { ExternalCell, ExternalCellObject, ExternalColumn, ExternalRow, ExternalSection } from "@console/shared";

const NUMERIC_FORMATS = ["money", "compact", "int", "percent", "signed", "decimal"];
/** A percent column's numbers are percents (56 is 56%), so a value past this is a unit mistake. */
export const PERCENT_LIMIT = 1000;

type Table = Extract<ExternalSection, { table: unknown }>["table"];

const isNumber = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v);

function spec(column: ExternalColumn): { label: string; format: string; tone: string; stack: boolean } {
  if (typeof column === "string") return { label: column, format: "text", tone: "none", stack: false };
  return {
    label: column.label,
    format: column.format ?? "text",
    tone: column.tone ?? "none",
    stack: (column.stack?.length ?? 0) > 0,
  };
}

export function rowCells(row: ExternalRow): ExternalCell[] {
  return Array.isArray(row) ? row : row.cells;
}

function valueOf(cell: ExternalCell): unknown {
  if (Array.isArray(cell)) return cell;
  if (typeof cell === "object" && cell !== null) return (cell as ExternalCellObject).value ?? null;
  return cell;
}

export function tableProblems(where: string, table: Table): string[] {
  const cols = table.columns.map(spec);
  const width = cols.length;
  const bad = table.rows.findIndex((row) => rowCells(row).length !== width);
  if (bad >= 0) return [`${where}.rows[${bad}]: ${rowCells(table.rows[bad]!).length} cells for ${width} columns`];
  const problems: string[] = [];
  cols.forEach((col, c) => {
    const name = `${where}.columns[${c}] ('${col.label}')`;
    const stackLength = (() => {
      const raw = table.columns[c]!;
      return typeof raw === "string" ? 0 : (raw.stack?.length ?? 0);
    })();
    if (col.tone === "sign" && !NUMERIC_FORMATS.includes(col.format)) {
      problems.push(`${name}: tone 'sign' needs a numeric format`);
    }
    if (col.stack && col.format !== "text") problems.push(`${name}: a stacked column takes no format`);
    table.rows.forEach((row, r) => {
      const cell = rowCells(row)[c] as ExternalCell;
      const at = `${where}.rows[${r}][${c}]`;
      const value = valueOf(cell);
      if (col.stack) {
        if (cell === null || value === null) return;
        if (!Array.isArray(value) || value.length !== stackLength) {
          problems.push(`${at}: a stacked cell is ${stackLength} numbers, one per segment`);
        } else if (value.some((n) => isNumber(n) && n < 0)) {
          problems.push(`${at}: a stacked segment cannot be negative`);
        }
        return;
      }
      if (Array.isArray(value)) problems.push(`${at}: an array is only for a stacked column`);
      else if (col.format === "percent" && isNumber(value) && Math.abs(value) > PERCENT_LIMIT) {
        problems.push(`${at}: ${value} is over ${PERCENT_LIMIT} (a percent column takes 56 for 56%)`);
      }
    });
  });
  const barProblem = (at: string, c: number): string[] => {
    if (cols[c]!.stack) return [`${at}: column ${c} ('${cols[c]!.label}') is stacked already`];
    if (table.rows.some((row) => typeof valueOf(rowCells(row)[c] as ExternalCell) === "string")) {
      return [`${at}: column ${c} ('${cols[c]!.label}') is not numeric`];
    }
    return [];
  };
  for (const col of table.bars ?? []) {
    const at = `${where}.bars`;
    if (col >= width) problems.push(`${at}: column ${col} does not exist (${width} columns)`);
    else problems.push(...barProblem(at, col));
  }
  table.columns.forEach((raw, c) => {
    if (typeof raw === "string" || raw.bar === undefined) return;
    const at = `${where}.columns[${c}].bar`;
    const scale = raw.bar.scale;
    if (scale !== undefined && !(scale[0] < scale[1])) {
      problems.push(`${at}.scale: min ${scale[0]} must be below max ${scale[1]}`);
    }
    problems.push(...barProblem(at, c));
  });
  return problems;
}
