import { CalendarHeatmap } from "../../components/CalendarHeatmap";
import { fmtMoney } from "../../lib/format";
import { useReview, useDesk } from "../../lib/api";

/**
 * The session heatmap and a compact end-of-day table, side by side beneath the equity chart --
 * folded into `EquityCard` (2026-09 no-scroll redesign) rather than kept as two more cards
 * stacked down the page. Net per trade is what turns the end-of-day net from a leaderboard
 * number into a read of how the session actually went.
 */
export function EquityBottomRow() {
  const { data: review } = useReview();
  const { data: desk } = useDesk();
  const days = review?.era.suiteDaily ?? [];
  const eod = desk?.eod;
  const eodRows = eod?.rows ?? [];

  return (
    <div className="equity-bottom-row">
      <div className="equity-bottom-col">
        <span className="fine-label">suite net by session</span>
        <CalendarHeatmap days={days.map((d) => ({ date: d.session, net: d.net, count: d.closed }))} countLabel="closed" />
      </div>
      <div className="equity-bottom-col">
        <span className="fine-label">end of day{eod?.session !== null && eod?.session !== undefined ? ` — ${eod.session}` : ""}</span>
        <table className="data-table num-from-1">
          <thead>
            <tr>
              <th>module</th>
              <th>net</th>
              <th>closed</th>
              <th>net / trade</th>
            </tr>
          </thead>
          <tbody>
            {eodRows.length === 0 ? (
              <tr>
                <td colSpan={4} className="muted">
                  no closed session yet
                </td>
              </tr>
            ) : (
              eodRows.map((r) => (
                <tr key={r.module}>
                  <td>{r.module}</td>
                  <td className={r.net !== null && r.net >= 0 ? "pnl-pos" : "pnl-neg"}>{fmtMoney(r.net)}</td>
                  <td className="muted">{r.closed ?? "—"}</td>
                  <td className={r.netPerTrade !== null ? (r.netPerTrade >= 0 ? "pnl-pos" : "pnl-neg") : "muted"}>
                    {fmtMoney(r.netPerTrade)}
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
