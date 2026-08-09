import type { ReactNode } from "react";

export function fmtMoney(v: number | null): string {
  if (v === null) return "—";
  const sign = v < 0 ? "-" : "";
  return `${sign}$${Math.abs(v).toFixed(2)}`;
}

export function fmtNum(v: number | null, digits = 2): string {
  return v === null ? "—" : v.toFixed(digits);
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

interface DataCardProps {
  title: string;
  headers: string[];
  loading: boolean;
  isError?: boolean;
  empty?: string;
  rowCount: number;
  skeletonRows?: number;
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
  children,
}: DataCardProps) {
  return (
    <section className={`card ${isError ? "card-stale" : ""}`}>
      <h2>{title}</h2>
      <div className="table-scroll">
        <table className="data-table">
          <thead>
            <tr>
              {headers.map((h) => (
                <th key={h}>{h}</th>
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
    </section>
  );
}
