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

export type SortDir = "asc" | "desc";

export interface SortState {
  key: string | null;
  dir: SortDir;
  toggle: (key: string) => void;
}

/** Column-sort state: first click sorts desc (numbers read best big-first), second flips, third clears. */
export function useSort(): SortState {
  const [key, setKey] = useState<string | null>(null);
  const [dir, setDir] = useState<SortDir>("desc");
  const toggle = (k: string) => {
    if (key !== k) {
      setKey(k);
      setDir("desc");
    } else if (dir === "desc") {
      setDir("asc");
    } else {
      setKey(null);
    }
  };
  return { key, dir, toggle };
}

/** Sort rows by the active column's accessor; nulls always sink to the bottom. */
export function sortRows<T>(
  rows: T[],
  sort: SortState,
  accessors: Record<string, (r: T) => number | string | null>,
): T[] {
  if (sort.key === null) return rows;
  const get = accessors[sort.key];
  if (get === undefined) return rows;
  const mul = sort.dir === "asc" ? 1 : -1;
  return [...rows].sort((a, b) => {
    const va = get(a);
    const vb = get(b);
    if (va === null && vb === null) return 0;
    if (va === null) return 1;
    if (vb === null) return -1;
    if (typeof va === "string" || typeof vb === "string") {
      return mul * String(va).localeCompare(String(vb));
    }
    return mul * (va - vb);
  });
}

/** Clickable header cell wired to a useSort state; shows ▲/▼ on the active column. */
export function SortTh({ label, k, sort }: { label: ReactNode; k: string; sort: SortState }) {
  const active = sort.key === k;
  return (
    <th
      onClick={() => sort.toggle(k)}
      style={{ cursor: "pointer", userSelect: "none", whiteSpace: "nowrap" }}
      aria-sort={active ? (sort.dir === "asc" ? "ascending" : "descending") : "none"}
      title="sort"
    >
      {label}
      {active ? (sort.dir === "asc" ? " ▲" : " ▼") : ""}
    </th>
  );
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
  /** Rendered under the table — pagers and per-table notes. */
  footer?: ReactNode;
  /** Dim the rows while a new page loads, without collapsing the card. */
  busy?: boolean;
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
  footer,
  busy = false,
  updatedAt,
  controls,
  children,
}: DataCardProps) {
  return (
    <Card title={title} updatedAt={updatedAt} isError={isError} controls={controls}>
      <div className={`table-scroll ${busy ? "table-busy" : ""}`}>
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
      {footer !== undefined && footer !== false && <div className="card-footer">{footer}</div>}
    </Card>
  );
}
