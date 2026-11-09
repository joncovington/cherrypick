import { useState } from "react";
import {
  feeDragPct,
  type WeeklyputCycle,
  type WeeklyputCycles,
  type WeeklyputHistoryRow,
  type WeeklyputPayload,
  type WeeklyputPosition,
  type WeeklyputStatus,
  type WeeklyputSummary,
} from "@console/shared";
import { Card, DataCard, PnlCell } from "../../components/DataTable";
import { TradeTotalsChip, tradeMoneyColumns } from "../../components/TradeMoney";
import { HistoryTable } from "../../components/table/HistoryTable";
import { useUrlDateRange } from "../../components/table/DateRange";
import type { ColumnDef } from "../../components/table/columns";
import { HelpSection, HelpWarn, type CardHelpContent } from "../../components/CardHelp";
import { Section } from "../../components/Section";
import { FliesTile } from "../Flies/FliesSession";
import { TimeLineChart } from "../../components/chart/TimeLineChart";
import { SERIES_COLORS } from "../../components/Charts";
import { DecisionsCard } from "../../components/DecisionsCard";
import { MeasurementBreaks } from "../../components/MeasurementBreaks";
import { ListViewContext, ViewSwitch, useViewChoice } from "../../components/ViewSwitch";
import { StatusLine } from "../../components/summary/ModuleSummary";
import { useWeeklyputHistory } from "../../lib/api";
import { fmtCash, fmtMoney, fmtNum } from "../../lib/format";
import { TradeCard, TradeCards } from "../Live/TradeCard";
import {
  ITEM_57_CYCLES,
  cumulativeBp,
  cycleRows,
  distanceToStrike,
  distanceWords,
  fmtBp,
  pickCycle,
  putModel,
} from "./weeklyputModels";
import { GuideLink } from "../../components/GuideLink";

/**
 * weeklyput's pages (packages/weeklyput): item 57's unhedged short put on SPX, SPY, QQQ and IWM,
 * paper only. The unit it is judged on is the CYCLE, one expiry, the equal-weight mean of net in bp
 * over the names that traded it. That mean, the forward summary and the calendar are the module's
 * own CLI answers (`/api/weeklyput/{cycles,summary,status}`); the ledger read (`/api/weeklyput`)
 * feeds the rest. The page derives display transforms and nothing else.
 *
 * Tones are signs, never verdicts. No figure from the study that declared the rule appears here:
 * those stay local, and the page says what paper adds, not what history found.
 */

function absentCard() {
  return (
    <div className="pf-layout">
      <Section concept="stand" tag="not started" question="Has this module run on this machine?">
        <Card title="weekly put" collapseKey="weeklyput-absent">
          <p className="muted">
            This module has not run on this machine: there is no paper store at{" "}
            <span className="mono">~/.cherrypick/data/weeklyput/paper_trades.db</span> yet. It fills in after the first
            entry window, in the last minutes of an entry session.
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

function bridgeError(error: string | null | undefined) {
  return error ? <p className="integrity-warn">{error}</p> : null;
}

const OUTCOME_CHIP: Record<string, string> = { entered: "chip chip-ok", missed: "chip chip-warn", pending: "chip", none: "chip" };

/** The open puts as a table: the strike, what it took in, where it marks, and how near spot is. */
function OpenPutsTable({ positions, loading, title = "open puts" }: { positions: WeeklyputPosition[]; loading: boolean; title?: string }) {
  const open = positions.filter((p) => p.status === "open").sort((a, b) => a.expiration.localeCompare(b.expiration) || a.symbol.localeCompare(b.symbol));
  return (
    <DataCard
      title={title}
      className="lv-12"
      headers={["name", "expiry", "strike", "entry Δ", "credit", "mark (close at mid)", "spot", "spot vs strike", "assignment"]}
      loading={loading}
      rowCount={open.length}
      numFrom={2}
      empty="no put open"
      help={oneNote(
        "Open puts",
        "How to read it",
        "Credit is the whole-position cash taken in at the mid, positive. Mark is what closing at the latest usable mid would cost, negative, shown only when a fresh quote exists. Spot vs strike is positive while the put is out of the money. Assignment-exposed means an ETF put is in the money with its time value under the module's threshold: early assignment is measured here, never modelled, and the result still books the put to expiry.",
      )}
    >
      {open.map((p) => {
        const d = distanceToStrike(p);
        return (
          <tr key={p.positionId}>
            <td>{p.symbol}</td>
            <td>{p.expiration}</td>
            <td>{fmtNum(p.strike, 2)}</td>
            <td>{p.entryDelta === null ? "—" : p.entryDelta.toFixed(3)}</td>
            <td>{fmtCash(p.entryValue)}</td>
            <td>{p.mark?.value == null ? "—" : fmtCash(p.mark.value)}</td>
            <td>{p.mark?.spot == null ? "—" : fmtNum(p.mark.spot, 2)}</td>
            <td className={d !== null && d < 0 ? "pnl-neg" : undefined} title={distanceWords(p)}>
              {d === null ? "—" : `${d >= 0 ? "+" : "-"}${(Math.abs(d) * 100).toFixed(2)}%`}
            </td>
            <td>{p.assignmentExposedAt !== null ? <span className="chip chip-warn">exposed</span> : <span className="muted">no</span>}</td>
          </tr>
        );
      })}
    </DataCard>
  );
}

export function WeeklyputToday({
  data,
  status,
  loading,
}: {
  data: WeeklyputPayload | undefined;
  status: WeeklyputStatus | undefined;
  loading: boolean;
}) {
  if (data !== undefined && !data.dbPresent) return absentCard();
  const entries = data?.entries ?? [];
  const positions = data?.positions ?? [];
  const cycle = pickCycle(status, entries);
  const rows = cycleRows(cycle, status?.names ?? [], entries, positions);
  const open = positions.filter((p) => p.status === "open");
  const exposed = open.filter((p) => p.assignmentExposedAt !== null).length;
  const entryToday = status?.entryToday ?? null;
  const calendarFailed = status !== undefined && !status.ok;
  return (
    <div className="pf-layout">
      <StatusLine module="weeklyput" mode="paper" />
      <Section concept="stand" tag="the day" question="Is today an entry session, and what comes next?">
        <FliesTile
          title="entry session"
          value={status === undefined || calendarFailed ? null : entryToday !== null ? "yes" : "no"}
          tone="dim"
          foot={
            status === undefined
              ? "reading the module's calendar"
              : calendarFailed
                ? "the module's calendar could not be read"
                : entryToday !== null
                  ? `selling into the ${entryToday} expiry · window ${status.decisionWindow ?? "—"}`
                  : `window ${status.decisionWindow ?? "—"} on an entry session`
          }
        />
        <FliesTile
          title="next cycle"
          value={status?.nextCycle ?? null}
          tone="dim"
          foot={status?.nextEntrySession ? `expiry; entered on ${status.nextEntrySession}` : undefined}
          to="/weeklyput/cycles"
          toLabel="cycles"
        />
        <FliesTile
          title="open puts"
          value={String(open.length)}
          tone="dim"
          foot={
            status && status.awaitingClose.length > 0
              ? `${String(status.awaitingClose.length)} past expiry, waiting for the posted close`
              : "held to the expiry's posted close"
          }
          to="/weeklyput/positions"
          toLabel="positions"
        />
        <FliesTile
          title="assignment-exposed"
          value={String(exposed)}
          tone={exposed > 0 ? "neg" : "dim"}
          foot="ETF puts in the money with little time value left: measured, not modelled"
        />
      </Section>
      {bridgeError(status?.error)}
      <Section concept="add" tag="this cycle" question={cycle === null ? "Which cycle is being traded?" : `How did each name do in the ${cycle} cycle?`}>
        <DataCard
          title={cycle === null ? "this cycle" : `cycle ${cycle}`}
          className="lv-12"
          headers={["name", "outcome", "why", "strike", "entry Δ", "credit"]}
          loading={loading}
          rowCount={rows.length}
          numFrom={3}
          empty="no cycle on the calendar yet"
        >
          {rows.map((r) => (
            <tr key={r.symbol}>
              <td>{r.symbol}</td>
              <td>
                <span className={OUTCOME_CHIP[r.outcome] ?? "chip"}>{r.outcome === "none" ? "no row yet" : r.outcome}</span>
              </td>
              <td className="muted" style={{ textAlign: "left" }}>
                {r.refusal ?? (r.outcome === "none" ? "the register has no row until the entry session" : "—")}
              </td>
              <td>{r.position === null ? "—" : fmtNum(r.position.strike, 2)}</td>
              <td>{r.position?.entryDelta == null ? "—" : r.position.entryDelta.toFixed(3)}</td>
              <td>{r.position === null ? "—" : fmtCash(r.position.entryValue)}</td>
            </tr>
          ))}
        </DataCard>
      </Section>
      <Section concept="stand" tag="the book" question="What is held, and how far is spot from each strike?">
        <OpenPutsTable positions={positions} loading={loading} />
      </Section>
    </div>
  );
}

/** A name's cell in the cycles table: net bp on top, whole-position dollars beneath. */
function NameCell({ cycle, symbol }: { cycle: WeeklyputCycle; symbol: string }) {
  const n = cycle.names[symbol];
  const u = cycle.unentered[symbol];
  if (n === undefined) {
    return u === undefined ? <span className="muted">—</span> : <span className="muted" title={u.refusal ?? undefined}>{u.outcome}</span>;
  }
  if (n.status !== "closed") return <span className="muted">open</span>;
  return (
    <span title={`${n.exitReason ?? ""} · strike ${String(n.strike)}`}>
      <span className={n.netBp !== null && n.netBp < 0 ? "pnl-neg" : "pnl-pos"}>{fmtBp(n.netBp)}</span>
      <br />
      <span className="muted">{n.netPnl === null ? "—" : fmtCash(n.netPnl)}</span>
    </span>
  );
}

export function WeeklyputCycles({ cycles, status }: { cycles: WeeklyputCycles | undefined; status: WeeklyputStatus | undefined }) {
  const rows = cycles?.cycles ?? [];
  const names = status?.names.length ? status.names : [...new Set(rows.flatMap((c) => [...Object.keys(c.names), ...Object.keys(c.unentered)]))];
  return (
    <div className="pf-layout">
      <Section concept="hist" tag="the judged unit" question="What did each expiry earn, name by name?">
        <p className="muted lv-12">
          A cycle, one expiry, is the unit this module is judged on: the equal-weight mean of net in basis points over the names that traded it. A name that
          was missed is left out of the mean, never counted as zero, and listed beside it.
        </p>
        {bridgeError(cycles?.error)}
        <DataCard
          title="cycles"
          className="lv-12"
          headers={["expiry", "entered", "missed", ...names, "cycle mean", "cycle net"]}
          loading={cycles === undefined}
          isError={cycles !== undefined && !cycles.ok}
          rowCount={rows.length}
          numFrom={3}
          empty="no cycle has been entered yet"
          help={{
            title: "Cycles",
            body: (
              <>
                <HelpSection title="One row an expiry">
                  <p>
                    Each name shows its net in basis points of the entry forward notional (100 times the forward), with the whole-position dollars under it.
                    The cycle mean is the equal-weight mean of those basis points over the names that entered; it appears once every put of the cycle has
                    settled.
                  </p>
                </HelpSection>
                <HelpSection title="Missed names">
                  <p>
                    A name not entered by the end of its window is recorded missed, with the last refusal a tick gave. Nothing fills after the window, so a
                    missed name is a cycle the paper arm could not trade, and it stays a visible row.
                  </p>
                </HelpSection>
              </>
            ),
          }}
        >
          {rows.map((c) => (
            <tr key={`${c.arm}-${c.expiration}`}>
              <td>{c.expiration}</td>
              <td>{c.entrySession}</td>
              <td className="muted" style={{ textAlign: "left" }}>
                {Object.keys(c.unentered).length === 0
                  ? "none"
                  : Object.entries(c.unentered)
                      .map(([s, u]) => `${s} (${u.outcome}${u.refusal ? `: ${u.refusal}` : ""})`)
                      .join(", ")}
              </td>
              {names.map((s) => (
                <td key={s}>
                  <NameCell cycle={c} symbol={s} />
                </td>
              ))}
              <td className={c.cycleNetBp !== null && c.cycleNetBp < 0 ? "pnl-neg" : c.cycleNetBp !== null ? "pnl-pos" : "muted"}>
                {c.finished ? fmtBp(c.cycleNetBp) : "not yet"}
              </td>
              <td>{c.finished ? <PnlCell v={c.netPnl} /> : <span className="muted">—</span>}</td>
            </tr>
          ))}
        </DataCard>
      </Section>
    </div>
  );
}

export function WeeklyputPerformance({ summary, cycles }: { summary: WeeklyputSummary | undefined; cycles: WeeklyputCycles | undefined }) {
  const arms = Object.entries(summary?.arms ?? {});
  const points = cumulativeBp(cycles?.cycles ?? []);
  const finished = arms.reduce((t, [, a]) => t + a.cycles, 0);
  const forward = finished < ITEM_57_CYCLES;
  return (
    <div className="pf-layout">
      {bridgeError(summary?.error)}
      <Section concept="edge" tag="the reading" question="Is this a verdict yet?">
        <Card title={forward ? "a forward read, not a verdict" : "cycles on file"} collapseKey="weeklyput-perf-bar" className="lv-12">
          <p>
            {String(finished)} finished {finished === 1 ? "cycle" : "cycles"} on file.{" "}
            {forward
              ? `Item 57's bar is ${String(ITEM_57_CYCLES)} cycles, and fewer than that is below it: what follows is a forward read of the paper arm, and it judges nothing.`
              : `That reaches item 57's bar of ${String(ITEM_57_CYCLES)} cycles; item 69's declared read against the study's replay is the verdict, and it lives in the research queue, not on this page.`}
          </p>
        </Card>
      </Section>
      <Section concept="stand" tag="per cycle" question="What has each cycle added up to?">
        {arms.length === 0 ? (
          <FliesTile title="finished cycles" value={summary === undefined ? null : "0"} tone="dim" foot="a cycle finishes once all its puts have settled" />
        ) : (
          arms.flatMap(([arm, a]) => [
            <FliesTile key={`${arm}-n`} title="finished cycles" value={String(a.cycles)} tone="dim" foot={`arm ${arm}${a.missed > 0 ? ` · ${String(a.missed)} ${a.missed === 1 ? "name" : "names"} missed` : ""}`} />,
            <FliesTile
              key={`${arm}-mean`}
              title="mean per cycle"
              value={a.meanBp === null ? null : fmtBp(a.meanBp)}
              tone={a.meanBp === null ? "dim" : a.meanBp >= 0 ? "pos" : "neg"}
              foot={a.stdevBp === null ? "equal-weight mean of the names that traded" : `spread (standard deviation) ${fmtBp(a.stdevBp).replace(/^\+/, "")}`}
            />,
            <FliesTile
              key={`${arm}-pos`}
              title="positive cycles"
              value={a.positiveShare === null ? null : `${(a.positiveShare * 100).toFixed(0)}%`}
              tone="dim"
              foot="share of finished cycles with a mean above zero"
            />,
            <FliesTile
              key={`${arm}-worst`}
              title="worst cycle"
              value={a.worst === null ? null : fmtBp(a.worst.cycleNetBp)}
              tone={a.worst === null ? "dim" : "neg"}
              foot={a.worst?.expiration}
            />,
          ])
        )}
      </Section>
      <Section concept="hist" tag="cumulative" question="How have the cycles added up, in order?">
        <Card
          title="cumulative per-cycle net, in bp"
          collapseKey="weeklyput-perf-cum"
          className="lv-12"
          help={oneNote(
            "Cumulative per-cycle net",
            "What the line is",
            "Each finished cycle adds its own equal-weight mean in basis points, oldest first. It is a running sum of the judged measure, not a compounding balance and not dollars. A cycle that has not finished is not on it.",
          )}
        >
          {points.length === 0 ? (
            <p className="muted">no cycle has finished yet</p>
          ) : (
            <TimeLineChart series={[{ label: "cumulative net (bp)", color: SERIES_COLORS[0], points }]} height={240} yFormat={(v) => `${v.toFixed(0)} bp`} />
          )}
        </Card>
      </Section>
      <Section concept="income" tag="by name" question="Which names carried it?">
        {arms.map(([arm, a]) => (
          <DataCard
            key={arm}
            title={`per-name mean net, arm ${arm}`}
            className="lv-12"
            headers={["name", "trades", "mean net"]}
            loading={false}
            rowCount={Object.keys(a.byName).length}
            numFrom={1}
            empty="no put has settled yet"
          >
            {Object.entries(a.byName).map(([sym, n]) => (
              <tr key={sym}>
                <td>{sym}</td>
                <td>{n.trades}</td>
                <td className={n.meanBp < 0 ? "pnl-neg" : "pnl-pos"}>{fmtBp(n.meanBp)}</td>
              </tr>
            ))}
          </DataCard>
        ))}
      </Section>
    </div>
  );
}

export function WeeklyputCosts({ summary, history }: { summary: WeeklyputSummary | undefined; history: WeeklyputHistoryRow[] | undefined }) {
  const rows = history ?? [];
  const arms = Object.values(summary?.arms ?? {});
  const sum = (pick: (m: WeeklyputSummary["arms"][string]["money"]) => number) => arms.reduce((t, a) => t + pick(a.money), 0);
  const have = arms.length > 0;
  const premium = sum((m) => m.entryValue);
  const drag = feeDragPct(sum((m) => m.fees), premium);
  const missed = arms.reduce((t, a) => t + a.missed, 0);
  return (
    <div className="pf-layout">
      <Section concept="risk" tag="costs" question="What did selling these puts cost?">
        <FliesTile title="fees" value={have ? fmtMoney(sum((m) => m.fees)) : null} tone="dim" foot="trading fees for the one sold leg" />
        <FliesTile title="settlement" value={have ? fmtMoney(sum((m) => m.settlement)) : null} tone="dim" foot="the cash-settlement charge on SPX; on an ETF, the assignment and the shares sold at the close" />
        <FliesTile title="slippage" value={have ? fmtMoney(sum((m) => m.slippage)) : null} tone="dim" foot="the fraction of the quoted spread given up at the sale, charged as a cost" />
        <FliesTile
          title="fee drag"
          value={drag === null ? null : `${drag.toFixed(1)}%`}
          tone="dim"
          foot={`trading fees over the ${have ? fmtMoney(premium) : "—"} of premium collected; settlement and slippage are apart`}
        />
        <FliesTile title="names missed" value={have ? String(missed) : null} tone="dim" foot="a name the window closed on is not traded later" />
      </Section>
      <Section concept="hist" tag="every put" question="What did each put pay out in costs?">
        <DataCard
          title="costs by put"
          className="lv-12"
          headers={["expiry", "name", "premium", "fees", "settlement", "slippage", "fee drag", "net"]}
          loading={history === undefined}
          rowCount={rows.length}
          numFrom={2}
          empty="no put has settled yet"
          help={oneNote(
            "Costs by put",
            "Three costs, three columns",
            "Fees, settlement and slippage are each their own column and each subtracted once: gross minus fees minus settlement minus slippage is the net on the history tab. Fee drag is the trading fees over the premium collected for that put.",
          )}
        >
          {rows.map((r) => {
            const d = feeDragPct(r.fees, r.entryCash);
            return (
              <tr key={r.positionId}>
                <td>{r.expiration}</td>
                <td>{r.symbol}</td>
                <td>{fmtCash(r.entryCash)}</td>
                <td>{r.fees === null ? "n/r" : fmtMoney(-r.fees)}</td>
                <td>{r.settlementFees === null ? "n/r" : fmtMoney(-r.settlementFees)}</td>
                <td>{r.slippage === null ? "n/r" : fmtMoney(-r.slippage)}</td>
                <td>{d === null ? "—" : `${d.toFixed(1)}%`}</td>
                <td>
                  <PnlCell v={r.netPnl} />
                </td>
              </tr>
            );
          })}
        </DataCard>
      </Section>
    </div>
  );
}

export function WeeklyputPositions({ data }: { data: WeeklyputPayload | undefined }) {
  const [view, setView] = useViewChoice("weeklyput-open", "cards");
  const open = (data?.positions ?? []).filter((p) => p.status === "open");
  if (data !== undefined && !data.dbPresent) return absentCard();
  return (
    <div className="pf-layout">
      <Section concept="stand" tag="the book" question="Which puts are open?">
        <OpenPutsTable positions={data?.positions ?? []} loading={data === undefined} />
      </Section>
      <Section concept="add" tag="the orders" question="What was sold, and where does each put stand?">
        <Card
          title="open puts as orders"
          className="lv-12"
          controls={open.length > 0 ? <ViewSwitch view={view} onChange={setView} /> : undefined}
          help={oneNote(
            "Open puts as orders",
            "The order chain",
            "Each put is drawn as the order that opened it: one sold-to-open leg at the mid it was sold at, then where it stands now. The close is at the expiry's posted close; there is no stop and no hedge.",
          )}
        >
          <ListViewContext.Provider value={view}>
            <TradeCards models={open.map(putModel)} empty="no put open" />
          </ListViewContext.Provider>
        </Card>
      </Section>
    </div>
  );
}

const MONEY = tradeMoneyColumns<WeeklyputHistoryRow>("the put sold at mid per share: a credit");

const HISTORY_COLUMNS: ColumnDef<WeeklyputHistoryRow>[] = [
  { id: "expiry", header: "expiry", kind: "describe", render: (r) => r.expiration, sortValue: (r) => r.expiration },
  { id: "symbol", header: "name", kind: "describe", render: (r) => r.symbol, sortValue: (r) => r.symbol },
  { id: "strike", header: "strike", kind: "describe", numeric: true, render: (r) => fmtNum(r.strike, 2), sortValue: (r) => r.strike },
  {
    id: "settled",
    header: "posted close",
    title: "the print the put settled against: the expiry's posted close, never the last trade",
    kind: "describe",
    numeric: true,
    render: (r) => fmtNum(r.settlePrice, 2),
    sortValue: (r) => r.settlePrice,
  },
  ...MONEY.describe.filter((c) => c.id !== "how"),
  { id: "how", header: "how", title: "expired, settled (SPX in the money, cash) or assigned (an ETF in the money)", kind: "describe", className: "muted", render: (r) => r.exitKind ?? "—", sortValue: (r) => r.exitKind },
  ...MONEY.money.flatMap((c): ColumnDef<WeeklyputHistoryRow>[] => {
    if (c.id === "exit") return [{ ...c, title: "minus the intrinsic value paid at the posted close, or 0 if it expired worthless" }];
    if (c.id === "slip") return [{ ...c, title: "the spread given up at the sale; charged as a cost and subtracted" }];
    if (c.id === "net")
      return [
        {
          ...c,
          title: "gross − fees − settlement − slippage",
        },
        {
          id: "bp",
          header: "net bp",
          title: "net over the entry forward notional (100 × forward): the measure the cycle mean is made of",
          kind: "describe",
          numeric: true,
          render: (r) => fmtBp(r.netBp),
          sortValue: (r) => r.netBp,
        },
      ];
    return [c];
  }),
];

/** The same columns with net last (the standard pins it) and the bp measure kept before the money. */
const HISTORY_DEFS: ColumnDef<WeeklyputHistoryRow>[] = (() => {
  const bp = HISTORY_COLUMNS.find((c) => c.id === "bp");
  const rest = HISTORY_COLUMNS.filter((c) => c.id !== "bp");
  const firstMoney = rest.findIndex((c) => c.kind === "money");
  return bp === undefined || firstMoney < 0 ? HISTORY_COLUMNS : [...rest.slice(0, firstMoney), bp, ...rest.slice(firstMoney)];
})();

export function WeeklyputHistory({ data }: { data: WeeklyputPayload | undefined }) {
  const { from, to } = useUrlDateRange();
  const history = useWeeklyputHistory({ arm: null, from, to });
  const byId = new Map((data?.positions ?? []).map((p) => [p.positionId, p]));
  const [open, setOpen] = useState<string | null>(null);
  return (
    <div className="pf-layout">
      <Section concept="hist" tag="the record" question="How did each put end?">
        <HistoryTable
          table="weeklyput-history"
          title="settled puts"
          className="view-fade lv-12"
          defs={HISTORY_DEFS}
          clientSort
          rows={history.data?.rows ?? []}
          rowKey={(r) => r.positionId}
          loading={history.isLoading}
          isError={history.isError}
          busy={history.isPlaceholderData}
          empty={from !== null || to !== null ? "no put expired in this date range" : "no put has settled yet"}
          updatedAt={history.data === undefined ? undefined : history.dataUpdatedAt}
          dateBasis="expiry"
          allTitle="every settled put on file"
          footer={history.data !== undefined && <TradeTotalsChip totals={history.data.totals} noun="puts" />}
          help={oneNote(
            "Settled puts",
            "Reading a row",
            "Entry is the credit taken in, exit the intrinsic value paid at the posted close (zero if it expired worthless), and they add up to gross. Gross less fees, settlement and slippage is net. Open a row to see the put as the order that sold it.",
          )}
          rowProps={(r) => ({
            className: "cal-row-click",
            onClick: () => setOpen(open === r.positionId ? null : r.positionId),
          })}
          expanded={(r) => {
            const p = open === r.positionId ? byId.get(r.positionId) : undefined;
            return p === undefined ? null : (
              <div className="tc-grid">
                <TradeCard m={putModel(p)} />
              </div>
            );
          }}
          detailClassName="cal-detail-row"
        />
      </Section>
      <MeasurementBreaks breaks={data?.measurementBreaks ?? []} />
    </div>
  );
}

export function WeeklyputDecisions() {
  return (
    <div className="pf-layout">
      <Section concept="add" tag="decisions" question="What did each name decide, and why?">
        <DecisionsCard module="weeklyput" />
      </Section>
    </div>
  );
}

export function WeeklyputHelp({ status }: { status: WeeklyputStatus | undefined }) {
  const window = status?.decisionWindow ?? "15:45–15:55 ET (12:45–12:55 on an early close)";
  return (
    <div className="pf-layout">
      <GuideLink to="/help/strategy-pages#weekly-put-weeklyput" />
      <Section concept="stand" tag="the rule" question="What does this module do?">
        <Card title="the weekly put" collapseKey="weeklyput-help-rule">
          <div className="pmcc-prose">
            <p>
              On each weekly expiry that is not a standard monthly, it sells the put nearest 10 delta on SPX, SPY, QQQ and IWM, seven calendar days before
              the expiry, and holds it to the expiry&apos;s posted close. One contract a name, sold at the mid. No hedge, no stop, no exit. A week whose
              seven-day trade would share a session with a monthly&apos;s is skipped. Paper only: there is no order path anywhere in the module.
            </p>
            <p>
              It is judged by the cycle, not the put: each expiry&apos;s equal-weight mean of net in basis points over the names that traded it.
            </p>
          </div>
        </Card>
      </Section>
      <Section concept="dir" tag="why" question="Why does it exist?">
        <Card title="from item 46 to item 69" collapseKey="weeklyput-help-why">
          <div className="pmcc-prose">
            <ol className="pmcc-rules">
              <li>Item 46 asked forty cells of short premium for an edge after costs. One passed: the equity-index 10-delta put at seven days, on the monthly expiries, hedged.</li>
              <li>Item 57 read the same trade on the weekly expiries item 46 never saw. The hedged arm failed. The unhedged arm passed every check it was held to.</li>
              <li>Item 69 is that unhedged arm in paper, reading forward against the study&apos;s own replay of the same trades. Its figures stay local; none is shown here.</li>
            </ol>
          </div>
        </Card>
      </Section>
      <Section concept="edge" tag="paper" question="What does paper add, and say?">
        <Card title="what paper adds" collapseKey="weeklyput-help-paper">
          <div className="pmcc-prose">
            <ol className="pmcc-rules">
              <li>
                The entry is a close-of-session fill, not an end-of-day quote. The window is {window}. A name not entered by the end of it is recorded
                missed for that cycle; nothing fills after the window.
              </li>
              <li>
                A 10-delta put that the quoted strikes do not bracket is refused (delta_not_bracketed) and retried on the next tick: the nearest quoted put
                would be a different trade.
              </li>
              <li>
                The settlement print is the posted close, never the last trade, which for an ETF runs after the bell. A Friday expiry therefore often
                settles on Monday morning.
              </li>
              <li>
                Early assignment is measured, never modelled. An in-the-money ETF put with little time value left is flagged assignment-exposed, and the
                result still books it to expiry, so the ETF names are an upper bound.
              </li>
              <li>No share is carried past the expiry session: an assigned ETF put is booked as shares sold at the same close.</li>
              <li>A refused mark and a missed cycle are both rows. Nothing is silent.</li>
            </ol>
          </div>
        </Card>
        <Card
          title="reading the performance tab"
          collapseKey="weeklyput-help-bar"
          help={{
            title: "Forward read",
            body: (
              <HelpWarn title="Not a verdict">
                <p>Fewer than {String(ITEM_57_CYCLES)} finished cycles is below item 57&apos;s bar. Until then the numbers are a forward read of the paper arm.</p>
              </HelpWarn>
            ),
          }}
        >
          <p className="muted">
            Costs are charged as the suite charges them: fees, settlement and slippage each in their own column. The money on every table adds up: entry
            plus exit is gross, and gross less the three costs is net.
          </p>
        </Card>
      </Section>
    </div>
  );
}
