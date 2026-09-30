/**
 * How the console renders a number. One home, so a dash means the same thing everywhere.
 *
 * These lived in `components/DataTable.tsx`, which meant anything that wanted to format a number
 * imported a TABLE component to do it — and 35 files did. DataTable re-exports them, so that
 * coupling is gone without touching a single call site.
 *
 * **A null is an em dash, never a zero.** That is the suite's recording rule surfacing in the UI:
 * "not recorded" and "was zero" are different facts, and a 0.00 where a measurement is missing is
 * the misleadingly-precise zero the ledgers already refuse to write.
 */

/** `-$1.50`, with the sign OUTSIDE the currency symbol. */
export function fmtMoney(v: number | null): string {
  if (v === null) return "—";
  const sign = v < 0 ? "-" : "";
  return `${sign}$${Math.abs(v).toFixed(2)}`;
}

/**
 * A signed cash flow in whole-position dollars: `+$178.75` received, `-$50.00` paid. The trade table
 * standard (console CLAUDE.md) signs every entry/exit this way so a row adds up left to right.
 */
export function fmtCash(v: number | null): string {
  if (v === null) return "—";
  if (v === 0) return "$0.00";
  return `${v > 0 ? "+" : "-"}$${Math.abs(v).toFixed(2)}`;
}

/**
 * A per-share net price, `1.79 cr` / `0.50 db` — the one per-share column in a trade table, marked
 * so it cannot be read as dollars. Positive is a credit, the same sign as `fmtCash`.
 */
export function fmtPrice(v: number | null, digits = 2): string {
  if (v === null) return "—";
  if (v === 0) return (0).toFixed(digits);
  return `${Math.abs(v).toFixed(digits)} ${v > 0 ? "cr" : "db"}`;
}

export function fmtNum(v: number | null, digits = 2): string {
  return v === null ? "—" : v.toFixed(digits);
}

/**
 * IV rank, stored as a 0-1 fraction, shown on its own scale: `45/100`. Never a percentage -- that is
 * how IV itself is shown, and a rank of 45 beside an IV of 45% reads as the same number.
 */
export function fmtIvr(v: number | null | undefined): string {
  return v === null || v === undefined ? "—" : `${Math.round(v * 100)}/100`;
}

export function fmtPct(v: number | null, digits = 0): string {
  return v === null ? "—" : `${v.toFixed(digits)}%`;
}

/** A signed percentage: `+1.20%` / `-1.20%`, for a change against a reference. */
export function fmtPctSigned(v: number | null, digits = 2): string {
  if (v === null) return "—";
  return `${v >= 0 ? "+" : ""}${v.toFixed(digits)}%`;
}

/** "12s", "4m 12s", "—" — an age in seconds, rendered compactly. */
export function ageLabel(seconds: number | null): string {
  if (seconds === null) return "—";
  if (seconds < 90) return `${Math.round(seconds)}s`;
  const m = Math.floor(seconds / 60);
  const s = Math.round(seconds % 60);
  return `${String(m)}m ${String(s)}s`;
}
