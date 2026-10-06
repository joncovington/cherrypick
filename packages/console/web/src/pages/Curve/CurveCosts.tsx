import type { CurvePayload } from "@console/shared";
import { DataCard, PnlCell } from "../../components/DataTable";
import { CostsView } from "../../components/costs/CostsView";
import { fmtMoney } from "../../lib/format";

/**
 * curve's costs page: the question that decides this module. Its edge is real before costs, and on
 * the 2026-10-05 analysis costs took about half of every credit (packages/curve/CLAUDE.md), so the
 * page leads with where the premium went, then how often the gates let an entry through, then the
 * delivered shares that bound nothing in the spread's max loss.
 */
export function CurveCosts({ data, updatedAt }: { data: CurvePayload | undefined; updatedAt?: number }) {
  return (
    <div className="cards cards-wide">
      <CostsView
        arms={data?.arms ?? []}
        since={data?.outcomesSince ?? null}
        sinceLabel="the latest boundary"
        sinceRows={data?.entryOutcomes ?? []}
        allRows={data?.entryOutcomesAll ?? []}
        loading={data === undefined}
        premiumNote="entry credit x 100 x quantity, closed cycles"
        gateNote="hook's no_hook_signal is its design, not a cost: it waits for a rare spike."
        updatedAt={updatedAt}
      />
      <DataCard
        title="delivered shares (assignment and exercise)"
        headers={["assigned", "position", "leg", "direction", "shares", "basis", "strike", "status", "disposed", "share P&L", "fees"]}
        loading={data === undefined}
        rowCount={data?.assignments.length ?? 0}
        numFrom={4}
        empty="none -- no leg has finished in the money and delivered shares"
      >
        {(data?.assignments ?? []).map((a) => (
          <tr key={`${a.positionId}-${a.legRole}`}>
            <td>{a.assignedSession}</td>
            <td className="mono">{a.positionId}</td>
            <td>{a.legRole}</td>
            <td>{a.direction}</td>
            <td>{a.shares}</td>
            <td>{a.basis === null ? "—" : a.basis.toFixed(2)}</td>
            <td>{a.strike === null ? "—" : a.strike.toFixed(2)}</td>
            <td>{a.status}</td>
            <td>{a.disposedSession ?? "—"}</td>
            <td>
              <PnlCell v={a.sharePnl} />
            </td>
            <td>{fmtMoney(a.fees === null ? null : -a.fees)}</td>
          </tr>
        ))}
      </DataCard>
    </div>
  );
}
