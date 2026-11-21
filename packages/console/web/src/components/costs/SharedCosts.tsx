import { Link } from "react-router-dom";
import type { GroupMoney, PerformanceModuleId, TradingMode } from "@console/shared";
import { Card, DataCard, PnlCell } from "../DataTable";
import { HelpSection, type CardHelpContent } from "../CardHelp";
import { Section } from "../Section";
import { FliesTile } from "../../pages/Flies/FliesSession";
import { useModulePerformance } from "../../lib/api";
import { fmtCash, fmtMoney } from "../../lib/format";

/**
 * A module's costs from the shared performance read (`core.metrics.money` per arm and for the module):
 * gross, then fees, settlement and slippage each in its own column, then net, and every row adds up
 * (`docs/conventions/money-layout.md`). For a module without a costs page of its own (earnings,
 * ladder). Nothing is summed here: the module's line is core's too.
 */

const HELP: CardHelpContent = {
  title: "Costs",
  body: (
    <>
      <HelpSection title="Three costs, three columns">
        <p>
          Fees, settlement and slippage are each their own column and each subtracted once: gross less fees less settlement less slippage is the
          net, on every row. The last row is every arm together.
        </p>
      </HelpSection>
      <HelpSection title="Unrecorded">
        <p>A trade whose total cost the ledger never recorded is counted but left out of the sums, so the columns still add up; it is never read as free.</p>
      </HelpSection>
    </>
  ),
};

function slip(m: GroupMoney): string {
  return m.slippage === null ? "in the fill" : fmtMoney(-m.slippage);
}

export function CostsBody({ groups, total, armLink }: { groups: Array<[string, GroupMoney]>; total: GroupMoney | null; armLink?: (arm: string) => string }) {
  const rows: Array<[string, GroupMoney]> = total ? [...groups, ["every arm", total]] : groups;
  const unrecorded = total?.unrecorded ?? 0;
  return (
    <>
      {total && (
        <>
          <FliesTile title="fees" value={fmtMoney(total.fees)} tone="dim" foot="trading fees, the open and the close of every leg" help={HELP} />
          <FliesTile title="settlement" value={fmtMoney(total.settlement)} tone="dim" foot="the charge on a position held to settlement" />
          <FliesTile
            title="slippage"
            value={total.slippage === null ? "in the fill" : fmtMoney(total.slippage)}
            tone="dim"
            foot={total.slippage === null ? "conceded inside the fill price, so it is in gross" : "the share of the quoted spread given up at the fills, charged as a cost"}
          />
        </>
      )}
      <DataCard
        title="costs by arm"
        className="lv-12"
        headers={["arm", "trades", "gross", "fees", "settlement", "slippage", "net"]}
        rowCount={rows.length}
        loading={false}
        numFrom={1}
        empty="no trade has closed yet"
        help={HELP}
      >
        {rows.map(([arm, m]) => (
          <tr key={arm} style={arm === "every arm" ? { fontWeight: 600 } : undefined}>
            <td>{armLink && arm !== "every arm" ? <Link to={armLink(arm)}>{arm}</Link> : arm}</td>
            <td>{m.trades}</td>
            <td>{fmtCash(m.gross)}</td>
            <td>{fmtMoney(-m.fees)}</td>
            <td>{fmtMoney(-m.settlement)}</td>
            <td>{slip(m)}</td>
            <td>
              <PnlCell v={m.net} />
            </td>
          </tr>
        ))}
      </DataCard>
      {unrecorded > 0 && (
        <p className="muted lv-12">
          {unrecorded} {unrecorded === 1 ? "trade has" : "trades have"} no recorded cost and {unrecorded === 1 ? "is" : "are"} left out of these sums.
        </p>
      )}
    </>
  );
}

export function SharedCosts({ module, mode = "paper", armLink }: { module: PerformanceModuleId; mode?: TradingMode; armLink?: (arm: string) => string }) {
  const { data } = useModulePerformance(module, "current", mode);
  const groups: Array<[string, GroupMoney]> = (data?.groups ?? []).flatMap((g) => (g.money ? [[g.tag, g.money] as [string, GroupMoney]] : []));
  return (
    <div className="pf-layout">
      <Section concept="risk" tag="costs" question="What did trading cost, and does each arm's money add up?">
        {data === undefined ? (
          <Card title="costs" help={HELP}>
            <p className="muted">Reading the costs…</p>
          </Card>
        ) : !data.ok ? (
          <Card title="costs" isError help={HELP}>
            <p className="muted">{data.error ?? "the costs read is unavailable"}</p>
          </Card>
        ) : (
          <CostsBody groups={groups} total={data.money ?? null} armLink={armLink} />
        )}
      </Section>
    </div>
  );
}
