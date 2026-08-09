import { useState, type ReactNode } from "react";

export function fmtMoney(v: number | null): string {
  if (v === null) return "—";
  const sign = v < 0 ? "-" : "";
  return `${sign}$${Math.abs(v).toFixed(2)}`;
}

export function fmtNum(v: number | null, digits = 2): string {
  return v === null ? "—" : v.toFixed(digits);
}

export function fmtPct(v: number | null, digits = 0): string {
  return v === null ? "—" : `${v.toFixed(digits)}%`;
}

export function PnlCell({ v }: { v: number | null }) {
  if (v === null) return <span className="muted">—</span>;
  return <span className={v >= 0 ? "pnl-pos" : "pnl-neg"}>{fmtMoney(v)}</span>;
}

export function SkeletonRows({ n, cols }: { n: number; cols: number }) {
  return (
    <>
      {Array.from({ length: n }, (_, i) => (
        <tr key={i}>
          <td colSpan={cols}>
            <span className="skeleton skeleton-text" style={{ width: `${50 + ((i * 19) % 40)}%` }} />
          </td>
        </tr>
      ))}
    </>
  );
}

/** "14:32:05" in ET — the per-card freshness stamp. */
export function asOfLabel(updatedAt: number | undefined): string | null {
  if (updatedAt === undefined || updatedAt === 0) return null;
  return new Date(updatedAt).toLocaleTimeString("en-US", { timeZone: "America/New_York", hour12: false });
}

const COLLAPSE_KEY = "cherrypick-console-collapsed-v1";

function readCollapsed(): Record<string, boolean> {
  try {
    return JSON.parse(localStorage.getItem(COLLAPSE_KEY) ?? "{}") as Record<string, boolean>;
  } catch {
    return {};
  }
}

/** Collapse state persisted per card title, like the old dashboards remembered layout. */
export function useCollapsed(key: string): [boolean, () => void] {
  const [collapsed, setCollapsed] = useState(() => readCollapsed()[key] === true);
  const toggle = () => {
    const next = !collapsed;
    setCollapsed(next);
    const all = readCollapsed();
    if (next) all[key] = true;
    else delete all[key];
    try {
      localStorage.setItem(COLLAPSE_KEY, JSON.stringify(all));
    } catch {
      /* storage unavailable — collapse still works for the session */
    }
  };
  return [collapsed, toggle];
}

interface CardProps {
  title: ReactNode;
  /** Stable key for persisted collapse; defaults to the title when it's a string. */
  collapseKey?: string;
  /** Query dataUpdatedAt — renders an "as of" stamp so staleness is never invisible. */
  updatedAt?: number;
  isError?: boolean;
  controls?: ReactNode;
  children: ReactNode;
}

/** Card shell with the house header: collapse caret, title, controls, freshness stamp. */
export function Card({ title, collapseKey, updatedAt, isError = false, controls, children }: CardProps) {
  const key = collapseKey ?? (typeof title === "string" ? title : "card");
  const [collapsed, toggle] = useCollapsed(key);
  const asOf = asOfLabel(updatedAt);
  return (
    <section className={`card ${isError ? "card-stale" : ""}`}>
      <div className="card-head">
        <button
          type="button"
          className="btn btn-quiet collapse-toggle"
          onClick={toggle}
          aria-expanded={!collapsed}
          aria-label={collapsed ? "expand" : "collapse"}
        >
          {collapsed ? "▸" : "▾"}
        </button>
        <h2>{title}</h2>
        {controls}
        {asOf !== null && (
          <span className="card-asof" title="last refreshed (ET)">
            as of {asOf}
          </span>
        )}
      </div>
      {!collapsed && children}
    </section>
  );
}

interface DataCardProps {
  title: string;
  headers: string[];
  loading: boolean;
  isError?: boolean;
  empty?: string;
  rowCount: number;
  skeletonRows?: number;
  /** Right-align every column from this index on — numbers read down a column. */
  numFrom?: number;
  /** Extra class on the table itself, for per-table column behavior. */
  tableClass?: string;
  updatedAt?: number;
  controls?: ReactNode;
  children: ReactNode;
}

/**
 * Card + table with the house loading behavior: renders at final size with
 * skeleton rows on first load, keeps stale data visible on refetch errors.
 */
export function DataCard({
  title,
  headers,
  loading,
  isError = false,
  empty = "no rows",
  rowCount,
  skeletonRows = 6,
  numFrom,
  tableClass = "",
  updatedAt,
  controls,
  children,
}: DataCardProps) {
  return (
    <Card title={title} updatedAt={updatedAt} isError={isError} controls={controls}>
      <div className="table-scroll">
        <table
          className={`data-table ${numFrom !== undefined ? `num-from-${Math.min(numFrom, 6)}` : ""} ${tableClass}`}
        >
          <thead>
            <tr>
              {headers.map((h, i) => (
                <th key={`${h}-${i}`}>{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {loading ? (
              <SkeletonRows n={skeletonRows} cols={headers.length} />
            ) : rowCount === 0 ? (
              <tr>
                <td colSpan={headers.length} className="muted">
                  {empty}
                </td>
              </tr>
            ) : (
              children
            )}
          </tbody>
        </table>
      </div>
    </Card>
  );
}
