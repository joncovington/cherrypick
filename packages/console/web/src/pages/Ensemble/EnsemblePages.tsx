import { useState } from "react";
import type { EnsembleHistoryRow, EnsembleLine, EnsemblePayload, EnsemblePerformance, EnsemblePosition } from "@console/shared";
import { Card, DataCard, PnlCell } from "../../components/DataTable";
import { TradeTotalsChip, tradeMoneyColumns } from "../../components/TradeMoney";
import { HistoryTable } from "../../components/table/HistoryTable";
import { useUrlDateRange } from "../../components/table/DateRange";
import type { ColumnDef } from "../../components/table/columns";
import { HelpSection, type CardHelpContent } from "../../components/CardHelp";
import { Section } from "../../components/Section";
import { FliesTile } from "../Flies/FliesSession";
import { TimeLineChart } from "../../components/chart/TimeLineChart";
import { SERIES_COLORS } from "../../components/Charts";
import { DecisionsCard } from "../../components/DecisionsCard";
import { MeasurementBreaks } from "../../components/MeasurementBreaks";
import { ListViewContext, ViewSwitch, useViewChoice } from "../../components/ViewSwitch";
import { StatusLine } from "../../components/summary/ModuleSummary";
import { GuideLink } from "../../components/GuideLink";
import { useEnsembleHistory } from "../../lib/api";
import { fmtCash, fmtMoney, fmtNum } from "../../lib/format";
import { TradeCard, TradeCards } from "../Live/TradeCard";
import { closedModel, dailyCurve, gateWords, openModel, shapeWords, todayCounts } from "./ensembleModels";

/**
 * ensemble's pages (packages/ensemble): research item 75's arms in paper. An arm is one configured
 * variant and the set of them is whatever the ledger names, so every arm on these pages comes from
 * the data. Money is the ledger's own: signed cash flow in whole-position dollars, entry + exit =
 * gross, gross - fees - settlement - slippage = net. "P&L" means net at the suite's slippage model;
 * the stress and mid nets are context and are labelled so. Tones are signs, never verdicts.
 */

function absentCard() {
  return (
    <div className="pf-layout">
      <Section concept="stand" tag="not started" question="Has this module run on this machine?">
        <Card title="ensemble" collapseKey="ensemble-absent">
          <p className="muted">
            This module has not run on this machine: there is no paper store at{" "}
            <span className="mono">~/.cherrypick/data/ensemble/paper_trades.db</span> yet. It fills in after the first arm&apos;s entry window.
          </p>
        </Card>
      </Section>
    </div>
  );
}

function oneNote(title: string, heading: string, text: string): CardHelpContent {
  return {
    title,
    body: (
      <HelpSection title={heading}>
        <p>{text}</p>
      </HelpSection>
    ),
  };
}

const OUTCOME_CHIP: Record<string, string> = { entered: "chip chip-ok", missed: "chip chip-warn", pending: "chip" };

const structure = (p: { legs: EnsemblePosition["legs"] }): string => shapeWords(p.legs);

/** The open structures as a table: what each took in, where it marks, and what it is worth now. */
function OpenTable({ positions, loading }: { positions: EnsemblePosition[]; loading: boolean }) {
  const open = positions.filter((p) => p.status === "open").sort((a, b) => a.arm.localeCompare(b.arm) || a.symbol.localeCompare(b.symbol));
  return (
    <DataCard
      title="open structures"
      className="lv-12"
      headers={["arm", "name", "expiry", "legs", "entry", "mark (close at mid)", "unrealised", "spot"]}
      loading={loading}
      rowCount={open.length}
      numFrom={4}
      empty="no structure open"
      help={oneNote(
        "Open structures",
        "How to read it",
        "Entry is the whole-position cash at the mid when it was entered: a credit is positive, a debit negative. Mark is what closing at the latest usable mid would bring, signed, shown only when a fresh quote exists. Unrealised is entry plus mark, before any cost; fees, settlement and slippage are charged when the structure closes.",
      )}
    >
      {open.map((p) => (
        <tr key={p.positionId}>
          <td>{p.arm}</td>
          <td>{p.symbol}</td>
          <td>{p.expiration}</td>
          <td className="muted" style={{ textAlign: "left" }}>
            {structure(p)}
          </td>
          <td>{fmtCash(p.entryValue)}</td>
          <td>{p.mark?.value == null ? "—" : fmtCash(p.mark.value)}</td>
          <td>
            <PnlCell v={p.unrealised} />
          </td>
          <td>{p.mark?.spot == null ? "—" : fmtNum(p.mark.spot, 2)}</td>
        </tr>
      ))}
    </DataCard>
  );
}

export function EnsembleToday({ data, loading }: { data: EnsemblePayload | undefined; loading: boolean }) {
  if (data !== undefined && !data.dbPresent) return absentCard();
  const today = data?.today ?? [];
  const positions = data?.positions ?? [];
  const counts = todayCounts(today);
  const open = positions.filter((p) => p.status === "open");
  return (
    <div className="pf-layout">
      <StatusLine module="ensemble" mode="paper" />
      <Section concept="stand" tag="the day" question="How did each arm that was due today do?">
        <FliesTile title="arms on file" value={data === undefined ? null : String(data.arms.length)} tone="dim" foot={data?.session ? `session ${data.session}` : "no session yet"} />
        <FliesTile title="entered" value={data === undefined ? null : String(counts.entered)} tone="dim" foot="arms that took a position today" />
        <FliesTile title="pending" value={data === undefined ? null : String(counts.pending)} tone="dim" foot="window not over: a gate may still open" />
        <FliesTile title="missed" value={data === undefined ? null : String(counts.missed)} tone={counts.missed > 0 ? "neg" : "dim"} foot="the window closed unentered; nothing fills later" />
        <FliesTile title="open structures" value={String(open.length)} tone="dim" foot="held to the posted close or to the arm's exit" to="/ensemble/positions" toLabel="positions" />
      </Section>
      <Section concept="add" tag="today's register" question="Which gate blocked each arm that did not enter?">
        <DataCard
          title={data?.session ? `arms due ${data.session}` : "arms due today"}
          className="lv-12"
          headers={["arm", "name", "outcome", "blocked by", "ticks"]}
          loading={loading}
          rowCount={today.length}
          numFrom={4}
          empty="no arm was due this session"
          help={oneNote(
            "Today's register",
            "Blocked by",
            "The engine's first failing check on the arm's last tick, with the value that check read, from the session register and the decision journal. Pending means the entry window is not over, so the same gate may give way on a later tick. Missed means the window closed with no entry. Ticks is how many times the journal recorded that refusal today.",
          )}
        >
          {today.map((r) => (
            <tr key={`${r.arm}-${r.symbol}`}>
              <td>{r.arm}</td>
              <td>{r.symbol}</td>
              <td>
                <span className={OUTCOME_CHIP[r.outcome] ?? "chip"}>{r.outcome}</span>
              </td>
              <td className="muted" style={{ textAlign: "left" }}>
                {r.outcome === "entered" ? "entered" : r.refusal === null ? "no tick yet" : gateWords(r)}
              </td>
              <td>{r.gate?.occurrences ?? "—"}</td>
            </tr>
          ))}
        </DataCard>
      </Section>
      <Section concept="stand" tag="the book" question="What is held, and what is it worth now?">
        <OpenTable positions={positions} loading={loading} />
      </Section>
    </div>
  );
}

export function EnsemblePositions({ data }: { data: EnsemblePayload | undefined }) {
  const [view, setView] = useViewChoice("ensemble-open", "cards");
  if (data !== undefined && !data.dbPresent) return absentCard();
  const open = (data?.positions ?? []).filter((p) => p.status === "open");
  const arms = [...new Set(open.map((p) => p.arm))].sort();
  return (
    <div className="pf-layout">
      <Section concept="stand" tag="the book" question="Which structures are open, arm by arm?">
        <OpenTable positions={data?.positions ?? []} loading={data === undefined} />
      </Section>
      <Section concept="add" tag="the orders" question="What was opened, and where does each stand?">
        {arms.length === 0 ? (
          <Card title="open structures as orders" className="lv-12" collapseKey="ensemble-open-empty">
            <p className="muted">no structure open</p>
          </Card>
        ) : (
          arms.map((arm, i) => (
            <Card
              key={arm}
              title={`arm ${arm}`}
              className="lv-12"
              controls={i === 0 ? <ViewSwitch view={view} onChange={setView} /> : undefined}
              help={oneNote(
                "Open structures as orders",
                "The order chain",
                "Each structure is drawn as the order that opened it: every leg, sold to open or bought to open, at the strike the arm placed it, then where it stands now. It closes on the arm's own exit or at the expiry's posted close.",
              )}
            >
              <ListViewContext.Provider value={view}>
                <TradeCards models={open.filter((p) => p.arm === arm).map(openModel)} />
              </ListViewContext.Provider>
            </Card>
          ))
        )}
      </Section>
    </div>
  );
}

const MONEY = tradeMoneyColumns<EnsembleHistoryRow>("the whole structure at the mid per share: a credit or a debit");

const HISTORY_DEFS: ColumnDef<EnsembleHistoryRow>[] = [
  { id: "arm", header: "arm", kind: "describe", render: (r) => r.arm, sortValue: (r) => r.arm },
  { id: "symbol", header: "name", kind: "describe", render: (r) => r.symbol, sortValue: (r) => r.symbol },
  { id: "entered", header: "entered", kind: "describe", render: (r) => r.entrySession, sortValue: (r) => r.entrySession },
  { id: "closed", header: "closed", kind: "describe", render: (r) => r.exitSession ?? "—", sortValue: (r) => r.exitSession },
  ...MONEY.describe.filter((c) => c.id !== "how"),
  {
    id: "how",
    header: "exit",
    title: "touch (spot hit the arm's level), profit_target, time (minutes before expiry) or settlement (held to the posted close)",
    kind: "describe",
    className: "muted",
    render: (r) => r.exitReason ?? "—",
    sortValue: (r) => r.exitReason,
  },
  ...MONEY.money.map((c): ColumnDef<EnsembleHistoryRow> => {
    if (c.id === "exit") return { ...c, title: "what closing brought at the mid (+) or cost (−), or the settlement intrinsic at the posted close" };
    if (c.id === "slip") return { ...c, title: "the spread given up at the fills; charged as a cost and subtracted once" };
    if (c.id === "net") return { ...c, title: "gross − fees − settlement − slippage, at the suite's slippage model" };
    return c;
  }),
];

export function EnsembleHistory({ data }: { data: EnsemblePayload | undefined }) {
  const { from, to } = useUrlDateRange();
  const history = useEnsembleHistory({ arm: null, from, to });
  const [open, setOpen] = useState<string | null>(null);
  return (
    <div className="pf-layout">
      <Section concept="hist" tag="the record" question="How did each structure end?">
        <HistoryTable
          table="ensemble-history"
          title="closed structures"
          className="view-fade lv-12"
          defs={HISTORY_DEFS}
          clientSort
          rows={history.data?.rows ?? []}
          rowKey={(r) => r.positionId}
          loading={history.isLoading}
          isError={history.isError}
          busy={history.isPlaceholderData}
          empty={from !== null || to !== null ? "no structure closed in this date range" : "no structure has closed yet"}
          updatedAt={history.data === undefined ? undefined : history.dataUpdatedAt}
          dateBasis="exit"
          allTitle="every closed structure on file"
          footer={history.data !== undefined && <TradeTotalsChip totals={history.data.totals} noun="structures" />}
          help={oneNote(
            "Closed structures",
            "Reading a row",
            "Entry is the cash at the mid when it was opened, exit what closing or settling brought, and they add up to gross. Gross less fees, settlement and slippage is net. Open a row to see the structure as the order that opened it, every leg in the chain.",
          )}
          rowProps={(r) => ({
            className: "cal-row-click",
            onClick: () => setOpen(open === r.positionId ? null : r.positionId),
          })}
          expanded={(r) =>
            open === r.positionId ? (
              <div className="tc-grid">
                <TradeCard m={closedModel(r)} />
              </div>
            ) : null
          }
          detailClassName="cal-detail-row"
        />
      </Section>
      <MeasurementBreaks breaks={data?.measurementBreaks ?? []} />
    </div>
  );
}

export function EnsembleDecisions() {
  return (
    <div className="pf-layout">
      <Section concept="add" tag="decisions" question="What did each arm decide, and why?">
        <DecisionsCard module="ensemble" />
      </Section>
    </div>
  );
}

const COMBINED = "all arms";

function perfRows(perf: EnsemblePerformance | undefined): Array<[string, EnsembleLine]> {
  return perf === undefined ? [] : [...Object.entries(perf.arms), [COMBINED, perf.combined]];
}

const tone = (v: number): "pos" | "neg" | "dim" => (v > 0 ? "pos" : v < 0 ? "neg" : "dim");

export function EnsemblePerformancePage({ perf }: { perf: EnsemblePerformance | undefined }) {
  const rows = perfRows(perf);
  const combined = perf?.combined;
  const series =
    perf === undefined
      ? []
      : [
          { label: COMBINED, color: SERIES_COLORS[0], points: dailyCurve(perf.combined) },
          ...Object.entries(perf.arms).map(([arm, line], i) => ({ label: arm, color: SERIES_COLORS[(i + 1) % SERIES_COLORS.length], points: dailyCurve(line) })),
        ];
  if (perf !== undefined && !perf.dbPresent) return absentCard();
  return (
    <div className="pf-layout">
      <Section concept="stand" tag="all arms" question="What have the arms earned together, and how far did that fall?">
        <FliesTile title="net P&L" value={combined === undefined ? null : fmtMoney(combined.net)} tone={combined === undefined ? "dim" : tone(combined.net)} foot="net at the suite's slippage model, every arm stacked as sized" />
        <FliesTile title="drawdown" value={combined === undefined ? null : combined.trades === 0 ? "—" : fmtMoney(-combined.drawdown)} tone={combined !== undefined && combined.drawdown > 0 ? "neg" : "dim"} foot="largest peak-to-trough fall of the running net, trades in exit order" />
        <FliesTile title="closed trades" value={combined === undefined ? null : String(combined.trades)} tone="dim" foot={combined === undefined || combined.trades === 0 ? undefined : `${String(combined.wins)} with a net above zero`} />
        <FliesTile title="net at stress fills" value={combined === undefined ? null : fmtMoney(combined.netStress)} tone="dim" foot="context: fills halfway from the mid to the natural fill" />
        <FliesTile title="net at mid fills" value={combined === undefined ? null : fmtMoney(combined.netMid)} tone="dim" foot="context: fills at the mid, before the slippage model" />
      </Section>
      <Section concept="hist" tag="running net" question="How has each arm's net run, trade by trade?">
        <Card
          title="running net by arm"
          collapseKey="ensemble-perf-curve"
          className="lv-12"
          help={oneNote(
            "Running net",
            "What the lines are",
            "Each line is the running sum of net over that arm's closed trades, in the order they ended, one point a session. The top line stacks every arm as sized. It is dollars, not a compounding balance, and a trade still open is not on it.",
          )}
        >
          {series.every((s) => s.points.length === 0) ? <p className="muted">no structure has closed yet</p> : <TimeLineChart series={series} height={260} yFormat={(v) => fmtMoney(v)} />}
        </Card>
      </Section>
      <Section concept="income" tag="by arm" question="Which arms carried it, and which fell furthest?">
        <DataCard
          title="net and drawdown by arm"
          className="lv-12"
          headers={["arm", "trades", "wins", "mean net", "worst trade", "net (verdict)", "net at stress", "net at mid", "drawdown"]}
          loading={perf === undefined}
          rowCount={rows.length}
          numFrom={1}
          empty="no arm has closed a trade yet"
          help={oneNote(
            "Net and drawdown by arm",
            "Which net",
            "Net is net at the suite's slippage model, the verdict. Net at stress takes fills halfway from the mid to the natural fill and net at mid takes them at the mid: both are context for how much the verdict leans on the fill assumption. Drawdown is the largest peak-to-trough fall of the running net over that arm's closed trades, in exit order; the last row stacks every arm, so its fall is not the sum or the larger of the arms'.",
          )}
        >
          {rows.map(([arm, l]) => (
            <tr key={arm} style={arm === COMBINED ? { fontWeight: 600 } : undefined}>
              <td>{arm}</td>
              <td>{l.trades}</td>
              <td>{l.wins}</td>
              <td>{l.mean === null ? "—" : fmtMoney(l.mean)}</td>
              <td>{l.worst === null ? "—" : fmtMoney(l.worst)}</td>
              <td>
                <PnlCell v={l.net} />
              </td>
              <td className="muted">{fmtMoney(l.netStress)}</td>
              <td className="muted">{fmtMoney(l.netMid)}</td>
              <td className={l.drawdown > 0 ? "pnl-neg" : "muted"}>{l.drawdown > 0 ? fmtMoney(-l.drawdown) : "—"}</td>
            </tr>
          ))}
        </DataCard>
      </Section>
    </div>
  );
}

export function EnsembleCosts({ perf }: { perf: EnsemblePerformance | undefined }) {
  const rows = perfRows(perf);
  const c = perf?.combined;
  return (
    <div className="pf-layout">
      <Section concept="risk" tag="costs" question="What did trading these arms cost?">
        <FliesTile title="fees" value={c === undefined ? null : fmtMoney(c.fees)} tone="dim" foot="trading fees, the open and the close of every leg" />
        <FliesTile title="settlement" value={c === undefined ? null : fmtMoney(c.settlement)} tone="dim" foot="the cash-settlement charge on a structure held to the posted close" />
        <FliesTile title="slippage" value={c === undefined ? null : fmtMoney(c.slippage)} tone="dim" foot="the fraction of the quoted spread given up at the fills, charged as a cost" />
      </Section>
      <Section concept="hist" tag="every arm" question="Does each arm's money add up?">
        <DataCard
          title="costs by arm"
          className="lv-12"
          headers={["arm", "trades", "gross", "fees", "settlement", "slippage", "net"]}
          loading={perf === undefined}
          rowCount={rows.length}
          numFrom={1}
          empty="no structure has closed yet"
          help={oneNote(
            "Costs by arm",
            "Three costs, three columns",
            "Fees, settlement and slippage are each their own column and each subtracted once: gross minus fees minus settlement minus slippage is the net, on every row. The last row stacks every arm.",
          )}
        >
          {rows.map(([arm, l]) => (
            <tr key={arm} style={arm === COMBINED ? { fontWeight: 600 } : undefined}>
              <td>{arm}</td>
              <td>{l.trades}</td>
              <td>{fmtCash(l.gross)}</td>
              <td>{fmtMoney(-l.fees)}</td>
              <td>{fmtMoney(-l.settlement)}</td>
              <td>{fmtMoney(-l.slippage)}</td>
              <td>
                <PnlCell v={l.net} />
              </td>
            </tr>
          ))}
        </DataCard>
      </Section>
    </div>
  );
}

export function EnsembleHelp() {
  return (
    <div className="pf-layout">
      <GuideLink to="/help/strategy-pages#ensemble-ensemble" />
      <Section concept="stand" tag="the rule" question="What does this module do?">
        <Card title="the ensemble" collapseKey="ensemble-help-rule">
          <div className="pmcc-prose">
            <p>
              An arm is one configured variant: an underlying, an expiry rule, the legs and where each strike is placed, an entry trigger, the weekdays,
              the event skips, the filter gates, the exits and the size. Each session the arms due that day are decided by one engine, in arm-name order,
              and an arm that passes every check enters once. It then closes on its own exit (a spot touch, a profit target, minutes before expiry) or is
              held to the expiry&apos;s posted close. Paper only: a position is a ledger row, never an order.
            </p>
            <p>The arms are not a fixed list. Any arm in the module&apos;s config trades with no change here, and appears on every page the moment the ledger names it.</p>
          </div>
        </Card>
      </Section>
      <Section concept="edge" tag="reading it" question="What do the pages show, and what do they not?">
        <Card title="what is a verdict and what is context" collapseKey="ensemble-help-reading">
          <div className="pmcc-prose">
            <ol className="pmcc-rules">
              <li>P&amp;L is net at the suite&apos;s slippage model. Net at stress and net at mid are context, labelled as such, and are not the result.</li>
              <li>Drawdown is the largest peak-to-trough fall of the running net over closed trades in the order they ended. The combined line stacks every arm as sized.</li>
              <li>An arm that was due and did not enter shows the first failing check and the value it read. Pending is a window still open; missed is a window that closed.</li>
              <li>Nothing fills after an arm&apos;s entry window, so a missed arm-day is a day the paper arm could not trade, and it stays a visible row.</li>
              <li>Money is whole-position dollars, signed: entry plus exit is gross, and gross less fees, settlement and slippage is net, on every row.</li>
            </ol>
          </div>
        </Card>
        <Card title="a forward read" collapseKey="ensemble-help-forward">
          <p className="muted">Paper reads these rules forward against the study that declared them. A few weeks of paper is a forward read, not a verdict, and no figure from the study appears here.</p>
        </Card>
      </Section>
    </div>
  );
}

