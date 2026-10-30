import { Fragment, type ReactNode } from "react";
import type { ViewMode } from "../lightbox/navGroups";
import { useViewMode } from "../lib/viewMode";
import type { SortHeader } from "./DataTable";

/**
 * One table column, declared once: its header and its cell come from the same entry, so dropping a
 * column for the simple view can never leave a header over the wrong cells (2026-10-21).
 *
 * `detailOnly` marks a column the simple view leaves out. A column that only matters when it says
 * something (Live → today's `working`) sets it from the rows: `detailOnly: !anyWorking`. A column
 * removed in both views is simply not declared.
 *
 * `secondary` (2026-10-30, docs/console-review-plan.md batch 3) takes a column out of the row in
 * EVERY view and into the row's expanded line (`SecondaryCells`). Only a table that has an expanded
 * line may use it: a secondary column in a table without one would be a figure no view shows.
 */
export interface Column<R> {
  key: string;
  label: string;
  /** Hover text on the header. */
  title?: string;
  /** Sorts by this accessor key when the table carries a sort state. */
  sort?: string;
  detailOnly?: boolean;
  secondary?: boolean;
  /** The whole cell, `<td>` included, so a column keeps its own class, title and alignment. */
  td: (row: R) => ReactNode;
}

/** The columns a view shows in the row: every one in detail, the ones not marked detail-only in
 *  simple; a secondary column in neither (it is in the expanded line). */
export function columnsFor<C extends { detailOnly?: boolean; secondary?: boolean }>(cols: readonly C[], view: ViewMode): C[] {
  const row = cols.filter((c) => c.secondary !== true);
  return view === "detail" ? row : row.filter((c) => c.detailOnly !== true);
}

/** The secondary columns, for a row's expanded line. */
export function secondaryOf<C extends { secondary?: boolean }>(cols: readonly C[]): C[] {
  return cols.filter((c) => c.secondary === true);
}

/** A row's secondary figures as a one-row table inside its expanded line: the same header and the same
 *  cell each column would have had in the row. */
export function SecondaryCells<R>({ cols, row }: { cols: readonly Column<R>[]; row: R }) {
  if (cols.length === 0) return null;
  return (
    <div className="table-scroll">
      <table className="data-table secondary-cells">
        <thead>
          <HeadRow cols={cols} />
        </thead>
        <tbody>
          <tr>
            <Cells cols={cols} row={row} />
          </tr>
        </tbody>
      </table>
    </div>
  );
}

/** `columnsFor` at the console's current view. Also the hook for a tile row or a card: anything
 *  carrying `detailOnly`. */
export function useColumns<C extends { detailOnly?: boolean; secondary?: boolean }>(cols: readonly C[]): C[] {
  return columnsFor(cols, useViewMode());
}

/** The headers a `DataCard` takes, from the columns shown. */
export function headersOf<R>(cols: readonly Column<R>[]): Array<string | SortHeader> {
  return cols.map((c) => (c.sort !== undefined ? { label: c.label, k: c.sort, title: c.title } : c.label));
}

/** A plain table's header row, from the columns shown. */
export function HeadRow<R>({ cols }: { cols: readonly Column<R>[] }) {
  return (
    <tr>
      {cols.map((c) => (
        <th key={c.key} title={c.title}>
          {c.label}
        </th>
      ))}
    </tr>
  );
}

/** One row's cells, in the columns' order. */
export function Cells<R>({ cols, row }: { cols: readonly Column<R>[]; row: R }) {
  return (
    <>
      {cols.map((c) => (
        <Fragment key={c.key}>{c.td(row)}</Fragment>
      ))}
    </>
  );
}
