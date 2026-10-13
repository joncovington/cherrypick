/**
 * Fee drag, the one definition every surface uses (root CLAUDE.md, "one money layout"): trading
 * fees -- commissions and exchange fees, never settlement -- divided by the premium collected at
 * entry. Shared because the server derives it for the readers and the web derives it for a few
 * tiles that sum their own rows, and two copies already disagreed once (fees ÷ gross P&L on flies,
 * fees-with-settlement ÷ credit on MEIC, while the costs page had it right).
 */

/**
 * Trading fees out of a ledger fee total that folds settlement in (flies, MEIC): the total less
 * settlement where the split was recorded. Where it never was, the total stays in fees -- the
 * standard's rule for an unrecorded split -- and the settlement column reads n/r beside it.
 */
export function tradingFees(feesTotal: number | null, settlementFees: number | null): number | null {
  if (feesTotal === null) return null;
  return settlementFees === null ? feesTotal : feesTotal - settlementFees;
}

/**
 * Fee drag as a percentage: trading fees ÷ premium collected × 100. Null -- an em dash, never 0 or
 * Infinity -- when either side is unknown or nothing was collected (a debit-only scope, no rows).
 */
export function feeDragPct(fees: number | null, premium: number | null): number | null {
  if (fees === null || premium === null) return null;
  if (!Number.isFinite(fees) || !Number.isFinite(premium) || premium <= 0) return null;
  return (fees / premium) * 100;
}

/**
 * A cost summed over rows some of which never recorded it: the sum when every row did, else null
 * (rendered n/r). A partial sum shown as the total reads as "these trades cost this much" when it
 * is only the recorded share -- the same reason a single unrecorded row reads n/r, never $0.
 */
export function sumIfAllRecorded(sum: number | null, recordedRows: number, rows: number): number | null {
  if (recordedRows < rows) return null;
  return sum ?? 0;
}
