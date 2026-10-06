import type { ReactNode } from "react";
import type { ArmMoneyRow, EntryOutcomes } from "@console/shared";
import { Card, DataCard, PnlCell, fmtPct } from "../DataTable";
import { StatTile } from "../grid/GridCard";
import { fmtMoney } from "../../lib/format";

/**
 * A module's costs page body (curve 2026-10-05, pmcc 2026-10-06): where the premium went, and how
 * often the gates let an entry through.
 *
 * Fee drag is trading fees ÷ premium collected (root CLAUDE.md), named that way; "all costs ÷
 * premium" adds settlement and slippage beside it, never inside it. Entry outcomes are counted per
 * SESSION (`readers/armCosts.ts`), so a gate that refused every tick is one session.
 */

const share = (part: number, whole: number): number | null => (whole > 0 ? (part / whole) * 100 : null);

function OutcomeRows({ rows }: { rows: EntryOutcomes[] }) {
  return (
    <>
      {rows.map((o) => {
        const top = Object.entries(o.refusals).sort((a, b) => b[1] - a[1]);
        return (
          <tr key={o.arm}>
            <td>{o.arm}</td>
            <td>{o.sessions}</td>
            <td>{o.entered}</td>
            <td>{o.fills}</td>
            <td>{fmtPct(share(o.entered, o.sessions), 0)}</td>
            <td className="muted">{top.length === 0 ? "—" : top.map(([reason, n]) => `${reason} ${String(n)}`).join(" · ")}</td>
          </tr>
        );
      })}
    </>
  );
}

const OUTCOME_HEADERS = ["arm", "sessions", "entered", "fills", "entry rate", "the refusals that ended the other sessions"];

export function CostsView({
  arms,
  slippageInGross = false,
  since,
  sinceLabel,
  sinceRows,
  allRows,
  loading,
  premiumNote,
  gateNote,
  updatedAt,
}: {
  arms: ArmMoneyRow[];
  /** The modelled fill already concedes slippage (flies, meic): it is inside gross, shown as a
   *  measure and never subtracted again. Otherwise it is a charged cost column. */
  slippageInGross?: boolean;
  since: string | null;
  /** What `since` is: "the boundary", "the era's first entry". */
  sinceLabel: string;
  sinceRows: EntryOutcomes[];
  allRows: EntryOutcomes[];
  loading: boolean;
  /** What "premium" is for this module. */
  premiumNote: string;
  /** What a reader should know about this module's gates. */
  gateNote: ReactNode;
  updatedAt?: number;
}) {
  const premium = arms.reduce((t, a) => t + a.premium, 0);
  const fees = arms.reduce((t, a) => t + a.fees, 0);
  const charged = (a: ArmMoneyRow) => a.fees + a.settlementFees + (slippageInGross ? 0 : a.slippage);
  const allCosts = arms.reduce((t, a) => t + charged(a), 0);
  const slipTotal = arms.reduce((t, a) => t + a.slippage, 0);
  const cycles = arms.reduce((t, a) => t + a.positions, 0);
  const sessionsSince = sinceRows.reduce((t, o) => Math.max(t, o.sessions), 0);
  return (
    <>
      <div className="grid-12">
        <StatTile label="fee drag" value={cycles === 0 ? null : fmtPct(share(fees, premium), 1)} tone="dim" foot="trading fees ÷ premium collected, closed positions" />
        {slippageInGross ? (
          <StatTile
            label="slippage ÷ premium"
            value={cycles === 0 ? null : fmtPct(share(slipTotal, premium), 1)}
            tone="dim"
            foot="conceded inside gross by the modelled fill -- a measure, not subtracted again"
          />
        ) : (
          <StatTile label="all costs ÷ premium" value={cycles === 0 ? null : fmtPct(share(allCosts, premium), 1)} tone="dim" foot="fees + settlement + slippage, each its own column below" />
        )}
        <StatTile label="cost per position" value={cycles === 0 ? null : fmtMoney(allCosts / cycles)} tone="dim" foot={`${String(cycles)} closed positions, every arm`} />
        <StatTile label="premium collected" value={cycles === 0 ? null : fmtMoney(premium)} tone="dim" foot={premiumNote} />
      </div>

      <DataCard
        title="where the premium went, by arm"
        headers={
          slippageInGross
            ? ["arm", "closed", "premium", "gross", "fees", "settle", "net", "fee drag", "slip (in gross)", "slip ÷ premium"]
            : ["arm", "closed", "premium", "gross", "fees", "settle", "slip", "net", "fee drag", "all costs ÷ premium"]
        }
        loading={loading}
        rowCount={arms.length}
        numFrom={1}
        empty="no position has closed yet"
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
            {!slippageInGross && <td>{fmtMoney(-a.slippage)}</td>}
            <td>
              <PnlCell v={a.netPnl} />
            </td>
            <td>{fmtPct(share(a.fees, a.premium), 1)}</td>
            {slippageInGross ? (
              <>
                <td className="muted">{fmtMoney(a.slippage)}</td>
                <td>{fmtPct(share(a.slippage, a.premium), 1)}</td>
              </>
            ) : (
              <td>{fmtPct(share(charged(a), a.premium), 1)}</td>
            )}
          </tr>
        ))}
      </DataCard>

      <DataCard
        title={since === null ? "how often the gates let an entry through" : `how often the gates let an entry through, since ${since} (${sinceLabel})`}
        headers={OUTCOME_HEADERS}
        loading={loading}
        rowCount={sinceRows.length}
        numFrom={1}
        empty={since === null ? "no entry attempt recorded" : `no session evaluated since ${since} yet`}
      >
        <OutcomeRows rows={sinceRows} />
      </DataCard>
      {since !== null && (
        <DataCard
          title="the same, over every session on file"
          headers={OUTCOME_HEADERS}
          loading={loading}
          rowCount={allRows.length}
          numFrom={1}
          empty="no entry attempt recorded"
        >
          <OutcomeRows rows={allRows} />
        </DataCard>
      )}
      <Card title="reading the gates" collapseKey="costs-gates-note">
        <p className="integrity-note">
          Counted per session, not per tick: a gate that refused all morning is one session here. A session is entered if
          any tick filled; otherwise it counts under its last refusal.
          {sessionsSince > 0 && sessionsSince < 20 && ` Only ${String(sessionsSince)} sessions since ${since ?? "then"} -- too few to judge a gate on.`}{" "}
          {gateNote}
        </p>
      </Card>
    </>
  );
}
