import { fmtMoney, fmtPct } from "../../components/DataTable";

/**
 * The widest leg spread this position was entered at, shown next to the yield it was entered for.
 *
 * These two numbers belong side by side because on deep-ITM legs they are the same order of
 * magnitude, and the yield alone reads as an edge when the spread says it is a cost. The first
 * session's TNA entry is the case that put this column here: 18.48% weekly yield — the best-looking
 * number on the page — against a leg quoted $3.55 wide to capture $22.50 of time value.
 *
 * The tint is not the spread percentage but the spread measured against what the structure was sold
 * to capture, because a wide market is only a problem relative to the edge being harvested. A single
 * crossing of half the widest spread is compared to the whole net time value: at parity or worse the
 * trade cannot pay for its own round trip, and that is worth seeing before the P&L confirms it.
 */
export function EntrySpreadCell({
  pct,
  abs,
  netTv,
}: {
  pct: number | null;
  abs: number | null;
  netTv: number | null;
}) {
  if (pct === null) return <span className="muted">—</span>;
  // Both sides in dollars per contract: one crossing of half the widest spread, against the net
  // time value the position was opened to collect.
  const halfSpread = abs === null ? null : (abs / 2) * 100;
  const edge = netTv === null ? null : netTv * 100;
  const ratio = halfSpread !== null && edge !== null && edge > 0 ? halfSpread / edge : null;
  const cls = ratio === null ? "" : ratio >= 1 ? "integrity-err" : ratio >= 0.5 ? "integrity-warn" : "";
  const title =
    halfSpread === null || edge === null
      ? "widest leg bid/ask spread at entry, as a share of that leg's mid"
      : `Widest leg spread at entry: ${fmtMoney(abs)}/share. One crossing of half of it is ` +
        `${fmtMoney(halfSpread)} against ${fmtMoney(edge)} of net time value the structure was opened to ` +
        `capture${ratio === null ? "" : ` — ${ratio.toFixed(1)}x`}. The round trip pays it twice.`;
  return (
    <span className={cls} title={title}>
      {fmtPct(pct * 100, 1)}
      {abs !== null && <span className="muted"> ({fmtMoney(abs)})</span>}
    </span>
  );
}
