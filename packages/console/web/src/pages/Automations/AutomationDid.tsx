import type { AutomationReading, AutomationsLine, ReadingExit } from "@console/shared";
import { Card, DataCard, PnlCell } from "../../components/DataTable";
import { HelpSection, type CardHelpContent } from "../../components/CardHelp";
import { useAutomationReading } from "../../lib/api";
import { fmtCash, fmtMoney } from "../../lib/format";
import { howEnded } from "./automationsModels";

/**
 * "What it did" on one automation's page, beside its record: how its trades ended against how the pinned
 * run's ended (`exits` of the backtest package's `read automation-reading`, the same read "Is it working?"
 * uses), and where the premium went (the automation's own line of the performance read: gross, each cost
 * in its own row, net). Every figure is the server's; the page only lays them out.
 */

const HELP: CardHelpContent = {
  title: "What it did",
  body: (
    <>
      <HelpSection title="How its trades ended">
        <p>
          Each way a paper trade closed, how many did and what they netted, beside the share of the pinned run's trades that closed the same way.
          The paper loop records the engine's own exit, so both columns count the same thing. A stop share well above the run's is worth a look
          long before the money says so.
        </p>
      </HelpSection>
      <HelpSection title="Where the premium went">
        <p>
          Gross is what the structures made at the fills; fees, settlement and slippage are each their own row and each subtracted once, so the
          rows add up to the net. Paper fills at the mid, so its slippage is the suite's model by construction, not a measured cost.
        </p>
      </HelpSection>
    </>
  ),
};

const pct = (v: number | null): string => (v === null ? "—" : `${String(Math.round(v * 100))}%`);

export function ExitsCard({ exits }: { exits: ReadingExit[] }) {
  const trades = exits.reduce((s, e) => s + e.trades, 0);
  return (
    <DataCard
      title="how its trades ended"
      className="lv-8"
      headers={["how it ended", "trades", "share", "the run", "net"]}
      rowCount={exits.length}
      loading={false}
      numFrom={1}
      empty="no exit recorded"
      help={HELP}
      controls={<span className="chip">{trades} trades</span>}
    >
      {exits.map((e) => (
        <tr key={e.kind}>
          <td>{howEnded(e.kind)}</td>
          <td>{e.trades}</td>
          <td>{pct(e.share)}</td>
          <td>{pct(e.run_share)}</td>
          <td>{e.trades === 0 ? "—" : <PnlCell v={e.net} />}</td>
        </tr>
      ))}
    </DataCard>
  );
}

export function PremiumCard({ line }: { line: AutomationsLine }) {
  return (
    <DataCard
      title="where the premium went"
      className="lv-4"
      headers={["line", "amount"]}
      rowCount={5}
      loading={false}
      numFrom={1}
      help={HELP}
      controls={<span className="chip">rows add up</span>}
    >
      <tr>
        <td>gross</td>
        <td>{fmtCash(line.gross)}</td>
      </tr>
      <tr>
        <td>fees</td>
        <td>{fmtMoney(-line.fees)}</td>
      </tr>
      <tr>
        <td>settlement</td>
        <td>{fmtMoney(-line.settlement)}</td>
      </tr>
      <tr>
        <td>slippage</td>
        <td>
          {fmtMoney(-line.slippage)} <span className="chip">the model</span>
        </td>
      </tr>
      <tr style={{ fontWeight: 600 }}>
        <td>net P&amp;L</td>
        <td>
          <PnlCell v={line.net} />
          {line.mean === null ? null : <span className="muted"> · {fmtMoney(line.mean)} a trade</span>}
        </td>
      </tr>
    </DataCard>
  );
}

/** Why there is no exits table beside the run, in words. */
export function noExitsWhy(data: AutomationReading | undefined, loading: boolean): string {
  const below = "each closed structure says how it ended below.";
  if (loading || data === undefined) return "Reading the run…";
  if (data.error !== null) return `The run could not be read: ${data.error}.`;
  if (data.pinned === null) return `No run is pinned, so there is nothing to set its exits beside; ${below}`;
  if (data.withheld) return `Its pinned run is licensed, and licensed results are not shown now; ${below}`;
  if (data.reading?.state === "no_reference") return `The pinned run is too short to set its exits beside; ${below}`;
  return `Paper has no closed trade the run can be set beside yet; ${below}`;
}

/** The two cards, each shown once it has something to say: exits need a readable pinned run and a closed trade. */
export function DidCards({ name, line }: { name: string; line: AutomationsLine | undefined }) {
  const reading = useAutomationReading(name);
  const exits = reading.data?.reading?.paper.exits ?? [];
  const closed = exits.some((e) => e.trades > 0);
  if ((line === undefined || line.trades === 0) && !closed) return null;
  return (
    <>
      {closed ? (
        <ExitsCard exits={exits} />
      ) : (
        <Card title="how its trades ended" collapseKey="automations-automation-exits" className="lv-8" help={HELP}>
          <p className="muted">{noExitsWhy(reading.data, reading.isLoading)}</p>
        </Card>
      )}
      {line !== undefined && line.trades > 0 && <PremiumCard line={line} />}
    </>
  );
}
