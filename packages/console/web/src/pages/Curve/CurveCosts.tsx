import type { CurveEntryOutcomes, CurvePayload } from "@console/shared";
import { Card, DataCard, PnlCell, fmtPct } from "../../components/DataTable";
import { StatTile } from "../../components/grid/GridCard";
import { fmtMoney } from "../../lib/format";

/**
 * curve's costs page: the question that decides this module. Its edge is real before costs, and
 * on the 2026-10-05 analysis costs took about half of every credit (packages/curve/CLAUDE.md), so
 * the page leads with where the premium went, then how often the gates let an entry through.
 *
 * Fee drag is trading fees ÷ premium collected (root CLAUDE.md), named that way; "all costs ÷
 * premium" adds settlement and slippage beside it, never inside it.
 */

const share = (part: number, whole: number): number | null => (whole > 0 ? (part / whole) * 100 : null);

function OutcomeRows({ rows }: { rows: CurveEntryOutcomes[] }) {
  return (
    <>
      {rows.map((o) => {
        const top = Object.entries(o.refusals).sort((a, b) => b[1] - a[1]);
        return (
          <tr key={o.arm}>
            <td>{o.arm}</td>
            <td>{o.sessions}</td>
            <td>{o.entered}</td>
            <td>{fmtPct(share(o.entered, o.sessions), 0)}</td>
            <td className="muted">
              {top.length === 0
                ? "—"
                : top.map(([reason, n]) => `${reason} ${String(n)}`).join(" · ")}
            </td>
          </tr>
        );
      })}
    </>
  );
}

export function CurveCosts({ data, updatedAt }: { data: CurvePayload | undefined; updatedAt?: number }) {
  const arms = data?.arms ?? [];
  const premium = arms.reduce((t, a) => t + a.premium, 0);
  const fees = arms.reduce((t, a) => t + a.fees, 0);
  const allCosts = arms.reduce((t, a) => t + a.fees + a.settlementFees + a.slippage, 0);
  const cycles = arms.reduce((t, a) => t + a.positions, 0);
  const since = data?.outcomesSince ?? null;
  const sinceRows = data?.entryOutcomes ?? [];
  const allRows = data?.entryOutcomesAll ?? [];
  const sessionsSince = sinceRows.reduce((t, o) => Math.max(t, o.sessions), 0);

  return (
    <div className="cards cards-wide">
      <div className="grid-12">
        <StatTile
          label="fee drag"
          value={cycles === 0 ? null : fmtPct(share(fees, premium), 1)}
          tone="dim"
          foot="trading fees ÷ premium collected, closed cycles"
        />
        <StatTile
          label="all costs ÷ premium"
          value={cycles === 0 ? null : fmtPct(share(allCosts, premium), 1)}
          tone="dim"
          foot="fees + settlement + slippage, each its own column below"
        />
        <StatTile
          label="cost per cycle"
          value={cycles === 0 ? null : fmtMoney(allCosts / cycles)}
          tone="dim"
          foot={`${String(cycles)} closed cycles, every arm`}
        />
        <StatTile
          label="premium collected"
          value={cycles === 0 ? null : fmtMoney(premium)}
          tone="dim"
          foot="entry credit x 100 x quantity, closed cycles"
        />
      </div>

      <DataCard
        title="where the premium went, by arm"
        headers={["arm", "cycles", "premium", "gross", "fees", "settle", "slip", "net", "fee drag", "all costs ÷ premium"]}
        loading={data === undefined}
        rowCount={arms.length}
        numFrom={1}
        empty="no cycle has closed yet"
        updatedAt={updatedAt}
      >
        {arms.map((a) => (
          <tr key={a.arm}>
            <td>{a.arm}</td>
            <td>{a.positions}</td>
            <td>{fmtMoney(a.premium)}</td>
            <td>
              <PnlCell v={a.grossPnl} />
            </td>
            <td>{fmtMoney(-a.fees)}</td>
            <td>{fmtMoney(-a.settlementFees)}</td>
            <td>{fmtMoney(-a.slippage)}</td>
            <td>
              <PnlCell v={a.netPnl} />
            </td>
            <td>{fmtPct(share(a.fees, a.premium), 1)}</td>
            <td>{fmtPct(share(a.fees + a.settlementFees + a.slippage, a.premium), 1)}</td>
          </tr>
        ))}
      </DataCard>

      <DataCard
        title={since === null ? "how often the gates let an entry through" : `how often the gates let an entry through, since ${since}`}
        headers={["arm", "sessions", "entered", "entry rate", "the refusals that ended the other sessions"]}
        loading={data === undefined}
        rowCount={sinceRows.length}
        numFrom={1}
        empty={since === null ? "no entry attempt recorded" : `no session evaluated since the ${since} boundary yet`}
      >
        <OutcomeRows rows={sinceRows} />
      </DataCard>
      {since !== null && (
        <DataCard
          title="the same, over every session on file (the structures before the boundary included)"
          headers={["arm", "sessions", "entered", "entry rate", "the refusals that ended the other sessions"]}
          loading={data === undefined}
          rowCount={allRows.length}
          numFrom={1}
          empty="no entry attempt recorded"
        >
          <OutcomeRows rows={allRows} />
        </DataCard>
      )}
      <Card title="reading the gates" collapseKey="curve-costs-gates">
        <p className="integrity-note">
          Counted per session, not per tick: the entry window ticks every minute, so a gate that refused all morning is
          one session here. A session is entered if any tick filled; otherwise it counts under its last refusal.
          {sessionsSince > 0 && sessionsSince < 20 && ` Only ${String(sessionsSince)} sessions since the boundary -- too few to judge a gate on.`}{" "}
          hook's no_hook_signal is its design, not a cost: it waits for a rare spike.
        </p>
      </Card>

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
