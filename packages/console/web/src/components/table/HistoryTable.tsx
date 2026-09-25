import { Fragment, type HTMLAttributes, type ReactNode } from "react";
import { Card, SkeletonRows } from "../DataTable";
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
}) {
  const cols = useColumnLayout(table, defs);
  const span = cols.columns.length;
  return (
    <Card title={title} updatedAt={updatedAt} isError={isError} className={className} collapseKey={`history-${table}`}>
      <div className="history-controls">
        <ColumnsMenu defs={defs} layout={cols.layout} onChange={cols.setLayout} onReset={cols.reset} isDefault={cols.isDefault} />
        {filters}
        <DateRangeBar basis={dateBasis} allLabel={allLabel} allTitle={allTitle} />
      </div>
      <div className={`table-scroll ${busy ? "table-busy" : ""}`}>
        <table className="data-table">
          <thead>
            <tr>
              {cols.columns.map((c) => (
                <th key={c.id} title={c.title} className={cellClass(c)}>
                  {c.header}
                </th>
              ))}
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
              rows.map((r) => {
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
