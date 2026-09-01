import { fmtMoney } from "./DataTable";

/**
 * Mark-to-market P&L for an open position, net of costs incurred so far.
 *
 * Shared by every module that shows open trades. The modules state one convention between them --
 * gross is mid-priced and cost-free, net is `gross - fees` -- so the cell that renders it should be
 * one cell, not one per page: four copies would be four chances to disagree about what net means.
 *
 * `fees` here is what has been INCURRED (entry, plus any roll or add-on entry). The settlement fee
 * is not in it, because settlement has not happened. Callers say so on the column header rather
 * than leaving a reader to assume the round trip is priced.
 */
export function UnrealisedPnlCell({
  gross,
  net,
  fees,
  detail,
}: {
  gross: number | null;
  net: number | null;
  fees: number | null;
  /** Anything module-specific worth having on hover — a cost to close, a roll count. */
  detail?: string;
}) {
  if (net === null) {
    return (
      <span className="muted" title="no usable mark for every leg yet -- not the same as a zero P&L">
        —
      </span>
    );
  }
  const parts = [
    `gross ${gross === null ? "—" : fmtMoney(gross)}`,
    fees === null ? null : `${fmtMoney(fees)} fees to date`,
    detail ?? null,
  ].filter((x): x is string => x !== null);
  return (
    <span className={net >= 0 ? "pnl-pos" : "pnl-neg"} title={parts.join(" · ")}>
      {fmtMoney(net)}
    </span>
  );
}
