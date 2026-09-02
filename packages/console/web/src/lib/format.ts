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

export function fmtNum(v: number | null, digits = 2): string {
  return v === null ? "—" : v.toFixed(digits);
}

export function fmtPct(v: number | null, digits = 0): string {
  return v === null ? "—" : `${v.toFixed(digits)}%`;
}

/** A signed percentage: `+1.20%` / `-1.20%`, for a change against a reference. */
export function fmtPctSigned(v: number | null, digits = 2): string {
  if (v === null) return "—";
  return `${v >= 0 ? "+" : ""}${v.toFixed(digits)}%`;
}
