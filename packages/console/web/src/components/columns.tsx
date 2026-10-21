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
 */
export interface Column<R> {
  key: string;
  label: string;
  /** Hover text on the header. */
  title?: string;
  /** Sorts by this accessor key when the table carries a sort state. */
  sort?: string;
  detailOnly?: boolean;
  /** The whole cell, `<td>` included, so a column keeps its own class, title and alignment. */
  td: (row: R) => ReactNode;
}

/** The columns a view shows: every one in detail, the ones not marked detail-only in simple. */
export function columnsFor<C extends { detailOnly?: boolean }>(cols: readonly C[], view: ViewMode): C[] {
  return view === "detail" ? [...cols] : cols.filter((c) => c.detailOnly !== true);
}

/** `columnsFor` at the console's current view. Also the hook for a tile row or a card: anything
 *  carrying `detailOnly`. */
export function useColumns<C extends { detailOnly?: boolean }>(cols: readonly C[]): C[] {
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
