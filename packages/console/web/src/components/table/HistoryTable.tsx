import { Fragment, type HTMLAttributes, type ReactNode } from "react";
import { Card, SkeletonRows, sortRows, useSort } from "../DataTable";
import { ColumnsMenu } from "./ColumnsMenu";
import { DateRangeBar } from "./DateRange";
import { useColumnLayout, type ColumnDef } from "./columns";

/**
 * A module's history table with the suite's controls (2026-09-25): the columns menu, the table's own
 * filters, and the date range in the page address -- one component so seven modules' histories hide,
 * reorder and date-filter the same way. Flies built these first and keeps its own trade-log card.
 *
 * The columns come from a `ColumnDef` list (columns.ts), so the money block's order and `net` last
 * are enforced by the resolver, not by each table remembering to.
 *
 * Sorting (2026-10-16) comes in two modes, and a table chooses one. `clientSort` is for a table
 * that holds every row: each column with a `sortValue` sorts here. A server-paged table passes
 * `sort`/`onSort` (`usePage().toggleSort`) and `serverSorts`, the column ids its reader can order
 * the WHOLE result by -- sorting one fetched page in the browser would say "largest loss" of 100
 * rows while calling it the history's. With neither, no header sorts.
 */
export function HistoryTable<R>({
  table,
  title,
  defs,
  rows,
  rowKey,
  loading,
  isError = false,
  busy = false,
  empty,
  updatedAt,
  filters,
  dateBasis,
  allLabel,
  allTitle,
  footer,
  rowProps,
  expanded,
  detailClassName,
  className,
  dateRange = true,
  sort,
  onSort,
  serverSorts,
  clientSort = false,
}: {
  /** The prefs key the column layout is kept under: `columns:<table>`. */
  table: string;
  title: ReactNode;
  defs: ColumnDef<R>[];
  rows: R[];
  rowKey: (row: R) => string;
  loading: boolean;
  isError?: boolean;
  busy?: boolean;
  empty: string;
  updatedAt?: number;
  /** The table's own filters (arm, symbol, outcome…), set between the columns menu and the dates. */
  filters?: ReactNode;
  /** Which date the range bounds, e.g. "closed" -- shown beside it, because modules differ. */
  dateBasis?: string;
  allLabel?: string;
  allTitle?: string;
  footer?: ReactNode;
  rowProps?: (row: R) => HTMLAttributes<HTMLTableRowElement>;
  /** A detail row under this row, spanning every visible column; null when collapsed. */
  expanded?: (row: R) => ReactNode | null;
  detailClassName?: string;
  className?: string;
  /** False for a table that is not a history over dates (one position's own legs): no date bar,
   *  which would filter nothing and read as though it did. */
  dateRange?: boolean;
  /** The server-side sort in force (`PageState.sort`/`dir`) and the click that cycles it. A column
   *  is a sort button only with both this and its own `sortKey`. */
  sort?: { sort?: string; dir?: "asc" | "desc" };
  onSort?: (key: string) => void;
  /** The column ids the server sorts by, for a server-paged table. */
  serverSorts?: readonly string[];
  /** Every row is in hand: sort in the browser by each column's `sortValue`. */
  clientSort?: boolean;
}) {
  const cols = useColumnLayout(table, defs);
  const span = cols.columns.length;
  const local = useSort();
  const server = !clientSort && onSort !== undefined;
  const sortable = (c: ColumnDef<R>): boolean =>
    clientSort ? c.sortValue !== undefined : server && (serverSorts ?? []).includes(c.id);
  const active = clientSort ? { sort: local.key ?? undefined, dir: local.dir } : sort;
  const click = (id: string) => (clientSort ? local.toggle(id) : onSort?.(id));
  const shown = clientSort
    ? sortRows(rows, local, Object.fromEntries(defs.flatMap((d) => (d.sortValue ? [[d.id, d.sortValue]] : []))))
    : rows;
  return (
    <Card title={title} updatedAt={updatedAt} isError={isError} className={className} collapseKey={`history-${table}`}>
      <div className="history-controls">
        <ColumnsMenu defs={defs} layout={cols.layout} onChange={cols.setLayout} onReset={cols.reset} isDefault={cols.isDefault} />
        {filters}
        {dateRange && <DateRangeBar basis={dateBasis} allLabel={allLabel} allTitle={allTitle} />}
      </div>
      <div className={`table-scroll ${busy ? "table-busy" : ""}`}>
        <table className="data-table">
          <thead>
            <tr>
              {cols.columns.map((c) => {
                if (!sortable(c)) {
                  return (
                    <th key={c.id} title={c.title} className={cellClass(c)}>
                      {c.header}
                    </th>
                  );
                }
                const on = active?.sort === c.id;
                return (
                  <th
                    key={c.id}
                    title={c.title}
                    className={cellClass(c)}
                    aria-sort={on ? (active?.dir === "asc" ? "ascending" : "descending") : undefined}
                  >
                    <button type="button" className="th-sort" onClick={() => click(c.id)}>
                      {c.header}
                      {on ? (active?.dir === "asc" ? " ▴" : " ▾") : ""}
                    </button>
                  </th>
                );
              })}
            </tr>
          </thead>
          <tbody>
            {loading ? (
              <SkeletonRows n={8} cols={span} />
            ) : rows.length === 0 ? (
              <tr>
                <td colSpan={span} className="muted">
                  {empty}
                </td>
              </tr>
            ) : (
              shown.map((r) => {
                const detail = expanded?.(r) ?? null;
                return (
                  <Fragment key={rowKey(r)}>
                    <tr {...rowProps?.(r)}>
                      {cols.columns.map((c) => (
                        <td key={c.id} className={cellClass(c, c.className)}>
                          {c.render(r)}
                        </td>
                      ))}
                    </tr>
                    {detail !== null && (
                      <tr className={detailClassName}>
                        <td colSpan={span}>{detail}</td>
                      </tr>
                    )}
                  </Fragment>
                );
              })
            )}
          </tbody>
        </table>
      </div>
      {footer !== undefined && footer !== null && footer !== false && <div className="card-footer">{footer}</div>}
    </Card>
  );
}

/** Numbers read down a column: money columns always, describe columns that say they are numeric. */
function cellClass<R>(c: ColumnDef<R>, extra?: string): string | undefined {
  const num = c.kind === "money" || c.numeric === true ? "num" : "";
  const cls = `${num} ${extra ?? ""}`.trim();
  return cls === "" ? undefined : cls;
}
