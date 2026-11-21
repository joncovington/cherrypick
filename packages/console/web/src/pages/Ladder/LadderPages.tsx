import { useState } from "react";
import { Link } from "react-router-dom";
import { armHref } from "../../components/armpage/ModuleArmPage";
import {
  LADDER_HOSPITAL_DAYS,
  type LadderArmDecl,
  type LadderHistoryRow,
  type LadderPayload,
  type LadderStatus,
  type LadderSummary,
} from "@console/shared";
import { Card, DataCard, PnlCell } from "../../components/DataTable";
import { TradeTotalsChip, tradeMoneyColumns } from "../../components/TradeMoney";
import { HistoryTable } from "../../components/table/HistoryTable";
import { useUrlDateRange } from "../../components/table/DateRange";
import type { ColumnDef } from "../../components/table/columns";
import { HelpSection, HelpWarn, type CardHelpContent } from "../../components/CardHelp";
import { Section } from "../../components/Section";
import { FliesTile } from "../Flies/FliesSession";
import { DecisionsCard } from "../../components/DecisionsCard";
import { GuideLink } from "../../components/GuideLink";
import { MeasurementBreaks } from "../../components/MeasurementBreaks";
import { StatusLine } from "../../components/summary/ModuleSummary";
import { useLadderHistory } from "../../lib/api";
import { ageLabel, fmtMoney } from "../../lib/format";
import { TradeCard } from "../Live/TradeCard";
import { bidFromLevel, lotModel, pctOfCost, price, targetText, whenEt } from "./ladderModels";

/**
 * The ladder's pages (packages/ladder): a share dip ladder (a paper book here; the live tab is `LadderLive.tsx`) on leveraged index funds, four
 * arms, one lot per row in the ledger. Today's next buy levels and the per-arm summary are the module's
 * own CLI answers (`/api/ladder/{status,summary}`); the ledger read (`/api/ladder`, `/history`) feeds the
 * lots. The page derives display transforms and nothing else.
 *
 * Nothing here says the rule has an edge: it did not pass its tests (the Help tab). Tones are signs,
 * never verdicts.
 */

function absentCard() {
  return (
    <div className="pf-layout">
      <Section concept="stand" tag="not started" question="Has this module run on this machine?">
        <Card title="ladder" collapseKey="ladder-absent">
          <p className="muted">
            This module has not run on this machine: there is no paper store at{" "}
            <span className="mono">~/.cherrypick/data/ladder/paper_trades.db</span> yet. It fills in after the first tick of the
            paper loop, once the ladder is switched on in Config.
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

/** The arms in the module's own order, from whichever read has them. */
function armNames(status: LadderStatus | undefined, summary: LadderSummary | undefined, decl: LadderArmDecl[] = []): string[] {
  const names = [...Object.keys(status?.arms ?? {}), ...decl.map((a) => a.arm), ...Object.keys(summary?.arms ?? {})];
  return [...new Set(names)];
}

export function LadderToday({
  data,
  status,
  loading,
}: {
  data: LadderPayload | undefined;
  status: LadderStatus | undefined;
  loading: boolean;
}) {
  if (data !== undefined && !data.dbPresent) return absentCard();
  const funds = Object.entries(status?.funds ?? {});
  const arms = armNames(status, undefined, data?.arms);
  const open = data?.openLots ?? [];
  const hospital = open.filter((l) => l.hospital).length;
  const iteration = data?.lastIteration ?? null;
  const rows = funds.flatMap(([symbol, f]) => arms.map((arm) => ({ symbol, arm, f, level: f.nextBuy[arm] ?? null })));
  return (
    <div className="pf-layout">
      <StatusLine module="ladder" mode="paper" />
      <Section concept="stand" tag="the day" question="Is the loop running, and what is held?">
        <FliesTile
          title="hours"
          value={status === undefined || !status.ok ? null : status.inHours ? "open" : "closed"}
          tone="dim"
          foot={status?.hours ?? "reading the module's clock"}
        />
        <FliesTile
          title="loop"
          value={iteration === null ? (data === undefined ? null : "no tick") : iteration.status}
          tone="dim"
          foot={iteration === null ? "no tick recorded yet" : `last tick ${ageLabel(iteration.ageSeconds)} ago · ${iteration.phase}`}
        />
        <FliesTile
          title="open lots"
          value={data === undefined ? null : String(open.length)}
          tone="dim"
          foot={hospital > 0 ? `${String(hospital)} past ${String(LADDER_HOSPITAL_DAYS)} days (the hospital)` : `none past ${String(LADDER_HOSPITAL_DAYS)} days`}
          to="/ladder/positions"
          toLabel="open lots"
        />
        {funds.map(([symbol, f]) => (
          <FliesTile
            key={symbol}
            title={symbol}
            value={f.bid === null ? null : price(f.bid)}
            tone="dim"
            foot={`bid · ask ${price(f.ask)} · previous close ${price(f.refClose)}`}
          />
        ))}
      </Section>
      {bridgeError(status?.error)}
      <Section concept="add" tag="next buys" question="Where would each arm buy next?">
        <DataCard
          title="next buy levels"
          className="lv-12"
          headers={["fund", "arm", "previous close", "next buy", "bid is"]}
          loading={loading || status === undefined}
          isError={status !== undefined && !status.ok}
          rowCount={rows.length}
          numFrom={2}
          empty="the module reported no fund"
          help={oneNote(
            "Next buy levels",
            "How to read it",
            "Each arm rests its next buy at a level under the previous close (the session arm steps further down within a session; the chain arm steps down from its last lot). The levels are the module's own, read from the stream cache with no broker call. Bid is is the latest bid against the level: above it means the fall has not come yet, at or under it means the level has been reached and the next tick fills it.",
          )}
        >
          {rows.map(({ symbol, arm, f, level }) => {
            const d = bidFromLevel(f.bid, level);
            return (
              <tr key={`${symbol}-${arm}`}>
                <td>{symbol}</td>
                <td>{arm}</td>
                <td>{price(f.refClose)}</td>
                <td>{price(level)}</td>
                <td className={d?.tone === "pos" ? "pnl-pos" : "muted"}>{d === null ? "—" : d.text}</td>
              </tr>
            );
          })}
        </DataCard>
      </Section>
    </div>
  );
}

export function LadderPositions({ data }: { data: LadderPayload | undefined }) {
  if (data !== undefined && !data.dbPresent) return absentCard();
  const open = data?.openLots ?? [];
  return (
    <div className="pf-layout">
      <Section concept="stand" tag="the book" question="Which lots are held, and where do they stand?">
        <DataCard
          title="open lots"
          className="lv-12"
          headers={["arm", "fund", "bought (ET)", "lot", "target", "held", "bid", "value at the bid"]}
          loading={data === undefined}
          rowCount={open.length}
          numFrom={3}
          empty="no lot is held"
          help={{
            title: "Open lots",
            body: (
              <>
                <HelpSection title="Reading a row">
                  <p>
                    A lot is shares bought at a limit under the previous close and waiting for its own target. Lot is shares times the price paid. Bid is the last
                    bid the loop marked, and value at the bid is shares times that bid, whole position and before the fees on a sale. There is no stop.
                  </p>
                </HelpSection>
                <HelpWarn title={`The hospital: past ${String(LADDER_HOSPITAL_DAYS)} days`}>
                  <p>
                    The Discord group calls a lot that has not sold within {String(LADDER_HOSPITAL_DAYS)} days its trade hospital and writes covered calls on it
                    until it recovers. Here it is only counted and flagged. Nothing acts on it: at under 100 shares a lot there is no call to write.
                  </p>
                </HelpWarn>
              </>
            ),
          }}
        >
          {open.map((l) => (
            <tr key={l.positionId}>
              <td>{l.arm}</td>
              <td>{l.symbol}</td>
              <td>{whenEt(l.buySession, l.buyTime)}</td>
              <td>
                {String(l.quantity)} × {price(l.buyPrice)}
              </td>
              <td>{targetText(l)}</td>
              <td>
                {String(l.daysHeld)} {l.daysHeld === 1 ? "day" : "days"}
                {l.hospital ? (
                  <>
                    {" "}
                    <span className="chip chip-warn" title={`held more than ${String(LADDER_HOSPITAL_DAYS)} days: the group's trade hospital`}>
                      hospital
                    </span>
                  </>
                ) : null}
              </td>
              <td title={l.lastMarkTime === null ? "not marked yet" : `marked ${whenEt(l.lastMarkTime.slice(0, 10), l.lastMarkTime)} ET`}>
                {price(l.lastBid)}
              </td>
              <td>
                {l.valueAtBid === null ? (
                  "—"
                ) : (
                  <>
                    {fmtMoney(l.valueAtBid)}{" "}
                    <span className={l.unrealisedAtBid !== null && l.unrealisedAtBid < 0 ? "pnl-neg" : "pnl-pos"} title="value at the bid less the cost">
                      ({l.unrealisedAtBid === null ? "—" : fmtMoney(l.unrealisedAtBid)})
                    </span>
                  </>
                )}
              </td>
            </tr>
          ))}
        </DataCard>
      </Section>
    </div>
  );
}

const MONEY = tradeMoneyColumns<LadderHistoryRow>("the shares bought, per share: a debit");

/** The standard's money block, with the sale's words in place of the options' ones. */
export const MONEY_COLUMNS: ColumnDef<LadderHistoryRow>[] = MONEY.money.map((c) => {
  if (c.id === "entry") return { ...c, title: "the purchase: shares times the price paid, negative" };
  if (c.id === "exit") return { ...c, title: "the sale: shares times the price sold at, positive" };
  if (c.id === "settle") return { ...c, title: "none for shares: recorded as zero" };
  if (c.id === "slip") return { ...c, title: "zero by construction: a fill is the resting limit, or the market on a gap" };
  if (c.id === "fees") return { ...c, title: "the clearing fee on the buy and the sale, and the SEC fee and FINRA TAF on the sale" };
  return c;
});

const HISTORY_DEFS: ColumnDef<LadderHistoryRow>[] = [
  { id: "symbol", header: "fund", kind: "describe", render: (r) => r.symbol, sortValue: (r) => r.symbol },
  { id: "arm", header: "arm", kind: "describe", render: (r) => r.arm, sortValue: (r) => r.arm },
  {
    id: "bought",
    header: "bought (ET)",
    kind: "describe",
    render: (r) => whenEt(r.buySession, r.buyTime),
    sortValue: (r) => r.buyTime,
  },
  {
    id: "sold",
    header: "sold (ET)",
    kind: "describe",
    render: (r) => whenEt(r.sellSession, r.sellTime),
    sortValue: (r) => r.sellTime,
  },
  {
    id: "held",
    header: "held",
    title: "calendar days from the buy session to the sale",
    kind: "describe",
    numeric: true,
    render: (r) => (r.holdDays === null ? "—" : `${String(r.holdDays)} d`),
    sortValue: (r) => r.holdDays,
  },
  {
    id: "prices",
    header: "buy → sell",
    title: "per share: the price paid, and the price sold at",
    kind: "describe",
    numeric: true,
    render: (r) => `${price(r.buyPrice)} → ${price(r.sellPrice)}`,
    sortValue: (r) => r.buyPrice,
  },
  {
    id: "pct",
    header: "net %",
    title: "net over the cost of the lot",
    kind: "describe",
    numeric: true,
    render: (r) => pctOfCost(r.netPct),
    sortValue: (r) => r.netPct,
  },
  ...MONEY_COLUMNS,
];

export function LadderHistory({ data }: { data: LadderPayload | undefined }) {
  const { from, to } = useUrlDateRange();
  const history = useLadderHistory({ arm: null, from, to });
  const [open, setOpen] = useState<string | null>(null);
  const rows = history.data?.rows ?? [];
  return (
    <div className="pf-layout">
      <Section concept="hist" tag="the record" question="How did each sold lot end?">
        <HistoryTable
          table="ladder-history"
          title="sold lots"
          className="view-fade lv-12"
          defs={HISTORY_DEFS}
          clientSort
          rows={rows}
          rowKey={(r) => r.positionId}
          loading={history.isLoading}
          isError={history.isError}
          busy={history.isPlaceholderData}
          empty={from !== null || to !== null ? "no lot sold in this date range" : "no lot has sold yet"}
          updatedAt={history.data === undefined ? undefined : history.dataUpdatedAt}
          dateBasis="sold"
          allTitle="every sold lot on file"
          footer={history.data !== undefined && <TradeTotalsChip totals={history.data.totals} noun="lots" />}
          help={oneNote(
            "Sold lots",
            "Reading a row",
            "Entry is the purchase (negative) and exit the sale (positive); they add up to gross. Gross less fees, settlement and slippage is net, and net % is net over the cost of the lot. Settlement and slippage are zero for shares filled at a resting limit. Open a row to see the lot as the buy and the sale that closed it.",
          )}
          rowProps={(r) => ({
            className: "cal-row-click",
            onClick: () => setOpen(open === r.positionId ? null : r.positionId),
          })}
          expanded={(r) =>
            open === r.positionId ? (
              <div className="tc-grid">
                <TradeCard m={lotModel(r)} />
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

function netTone(v: number): string {
  return v < 0 ? "pnl-neg" : "pnl-pos";
}

export function LadderArms({
  summary,
  status,
  decl = [],
}: {
  summary: LadderSummary | undefined;
  status: LadderStatus | undefined;
  decl?: LadderArmDecl[];
}) {
  const names = armNames(status, summary, decl);
  const arms = names.filter((n) => summary?.arms[n] !== undefined);
  return (
    <div className="pf-layout">
      {bridgeError(summary?.error)}
      <Section concept="dir" tag="the arms" question="What does each arm do?">
        <DataCard
          title="arms on this machine"
          className="lv-12"
          headers={["arm", "entry", "target", "what it does"]}
          loading={false}
          rowCount={decl.length}
          empty="no arm is declared in the ladder's config"
        >
          {decl.map((a) => (
            <tr key={a.arm} className={a.enabled === false ? "muted" : undefined}>
              <td>
                <Link to={armHref("ladder", a.arm)}>{a.arm}</Link>
                {a.enabled === false ? " (off)" : ""}
              </td>
              <td>{a.entry ?? status?.arms[a.arm]?.entry ?? "—"}</td>
              <td>{a.target ?? status?.arms[a.arm]?.target ?? "—"}</td>
              <td className="muted" style={{ textAlign: "left" }}>
                {a.summary ?? "—"}
              </td>
            </tr>
          ))}
        </DataCard>
      </Section>
      <Section concept="edge" tag="the reading" question="How is each arm doing so far?">
        <DataCard
          title="by arm"
          className="lv-12"
          headers={["arm", "lots", "sold", "open", "realised net", "unrealised at the bid", "total", `sold within ${String(LADDER_HOSPITAL_DAYS)} d`, `open past ${String(LADDER_HOSPITAL_DAYS)} d`]}
          loading={summary === undefined}
          isError={summary !== undefined && !summary.ok}
          rowCount={arms.length}
          numFrom={1}
          empty="no lot has been bought yet"
          help={{
            title: "By arm",
            body: (
              <>
                <HelpSection title="The figures">
                  <p>
                    Each arm keeps its own book, so the rows are alternatives run side by side, not parts of one portfolio, and are not added together.
                    Realised net is the sold lots after fees. Unrealised at the bid is the open lots valued at the last bid, before the fees on a sale.
                    Total is the two together.
                  </p>
                </HelpSection>
                <HelpSection title="In the group's terms">
                  <p>
                    Sold within {String(LADDER_HOSPITAL_DAYS)} days and open past {String(LADDER_HOSPITAL_DAYS)} days are the group&apos;s own scoreboard. A
                    lot sold under a no-stop rule is always a winner, so the win rate is always 100% and says nothing: how much is still open, and for how
                    long, is the number to watch.
                  </p>
                </HelpSection>
              </>
            ),
          }}
        >
          {arms.map((arm) => {
            const a = summary!.arms[arm]!;
            const total = Math.round((a.netPnl + a.unrealisedAtBid) * 100) / 100;
            return (
              <tr key={arm}>
                <td title={status?.arms[arm] === undefined ? undefined : `${status.arms[arm]!.entry} entry, ${status.arms[arm]!.target} target`}>{arm}</td>
                <td>{a.lots}</td>
                <td>{a.closed}</td>
                <td>{a.open}</td>
                <td className={netTone(a.netPnl)} title={`gross ${fmtMoney(a.grossPnl)} less fees ${fmtMoney(a.fees)}`}>
                  {fmtMoney(a.netPnl)}
                </td>
                <td>
                  <PnlCell v={a.unrealisedAtBid} />
                </td>
                <td>
                  <PnlCell v={total} />
                </td>
                <td>{a.soldWithin18d}</td>
                <td className={a.inHospital > 0 ? "pnl-neg" : undefined} title={a.oldestOpenDays === null ? undefined : `oldest open lot: ${String(a.oldestOpenDays)} days`}>
                  {a.inHospital}
                </td>
              </tr>
            );
          })}
        </DataCard>
      </Section>
    </div>
  );
}

export function LadderDecisions() {
  return (
    <div className="pf-layout">
      <Section concept="add" tag="decisions" question="What did each arm decide, and why?">
        <DecisionsCard module="ladder" />
      </Section>
    </div>
  );
}

export function LadderHelp({ status }: { status: LadderStatus | undefined }) {
  const hours = status?.hours ?? "08:15-20:00 ET";
  return (
    <div className="pf-layout">
      <GuideLink to="/help/strategy-pages#ladder-ladder" />
      <Section concept="edge" tag="read this first" question="Does the rule work?">
        <Card title="not a tested edge" collapseKey="ladder-help-edge">
          <HelpWarn title="Every arm failed against random entry">
            <p>
              This is a rule a Discord group trades by hand. It failed its test on daily bars and again on minute bars, and paper runs it anyway to watch it trade at real quotes.
              A sold lot is always a winner under a rule with no stop, so a long run of green rows proves nothing: what counts is how much is still open, and
              how long it has been open.
            </p>
          </HelpWarn>
          <div className="pmcc-prose">
            <ol className="pmcc-rules">
              <li>
                <strong>Item 26, on daily bars: failed every arm.</strong> A small edge over random entry, lots held for years in a long fall, and buy-and-hold
                on the index funds beat it.
              </li>
              <li>
                <strong>Item 27, the decay target: passed</strong>, as a fix for the long holds. It shortens the wait; it does not make the entry an edge.
              </li>
              <li>
                <strong>Item 95, on minute bars (2016 to 2026): failed every arm</strong> against its random-entry control. The session arm missed only the
                Holm correction across the four arms.
              </li>
              <li>
                <strong>Item 96, adding the overnight hours: a provisional pass</strong>, carried by the commodity and China funds, with no gain on TQQQ.
              </li>
            </ol>
          </div>
        </Card>
      </Section>
      <Section concept="stand" tag="the rule" question="What does this module do?">
        <Card title="a lot at a time" collapseKey="ladder-help-rule">
          <div className="pmcc-prose">
            <p>
              On each leveraged fund (TQQQ and SOXL) it buys a lot of whole shares when the price is 7% under a reference, and sells each lot on its own at 4%
              over what that lot cost. No stop and no time limit. These pages are the paper book. The module also has a live path that ships in dry-run and off; the live tab only reads it, and arming is the /live-ladder-start command.
            </p>
            <p>
              Each arm is one entry rule paired with one target rule, and each keeps its own book. The arms on this machine, each with the
              config&apos;s own description, are listed on the arms tab. The entry rules:
            </p>
            <ol className="pmcc-rules">
              <li>
                <strong>session</strong>: every session starts again from the previous close. The first lot is bought at 93% of it, the second at 93% of that
                again, and so on, each level once per session even after its lot sells. This is the group&apos;s own rule.
              </li>
              <li>
                <strong>chain</strong>: with no lot open, the first lot is 7% under the previous close; after that each further lot is 7% under the last lot
                bought, in the same session or a later one. A sold lot does not move the anchor; an empty book resets it.
              </li>
              <li>
                <strong>once</strong>: at most one lot per session, 7% under the previous close.
              </li>
            </ol>
            <p>The target rules:</p>
            <ol className="pmcc-rules">
              <li>
                <strong>fixed</strong>: +4% of the lot&apos;s cost.
              </li>
              <li>
                <strong>decay</strong>: +4% for the first 30 days, then breakeven after the sale&apos;s fees from day 31, then 95% of cost from day 121
                (calendar days from the buy session).
              </li>
            </ol>
            <p>
              The reference is the previous session&apos;s posted close, never a last trade. A buy level rounds down to the cent and a target rounds up, so a
              rounded limit never fills on worse terms than the rule&apos;s own number.
            </p>
          </div>
        </Card>
      </Section>
      <Section concept="add" tag="paper" question="What does paper add, and say?">
        <Card title="fills" collapseKey="ladder-help-fills">
          <div className="pmcc-prose">
            <ol className="pmcc-rules">
              <li>
                Each level is a resting limit read off one quote per minute. A buy fills when the ask is at or under its level, a sale when the bid is at or over
                its target.
              </li>
              <li>
                The fill is the limit when the price crossed it since the previous quote (the order was resting as the market moved through it), and the market
                (the ask or the bid) when the market was already through it: a gap into the session, or an order placed under water.
              </li>
              <li>A touch shorter than one quote is missed. Slippage is zero by construction.</li>
              <li>
                A lot can be bought and sold in the same session, and a sold lot frees its slot at once. Hours are {hours}, but the stream cache only holds quotes
                while the streamer runs, so nothing fills overnight.
              </li>
            </ol>
          </div>
        </Card>
        <Card title="fees" collapseKey="ladder-help-fees">
          <div className="pmcc-prose">
            <p>
              Tastytrade&apos;s 2026 stock schedule: no commission, a clearing fee of $0.0008 per share on the buy and on the sale, and on the sale only the SEC
              fee and the FINRA trading activity fee. The money on every table adds up: entry plus exit is gross, and gross less fees, settlement and
              slippage is net. Settlement and slippage are zero for shares.
            </p>
          </div>
        </Card>
        <Card title="the hospital" collapseKey="ladder-help-hospital">
          <div className="pmcc-prose">
            <p>
              The group&apos;s &quot;trade hospital&quot; is a lot not sold within {String(LADDER_HOSPITAL_DAYS)} days. Here it is counted and flagged, never
              acted on: the group writes covered calls on those lots, and at under 100 shares a lot that is not possible.
            </p>
          </div>
        </Card>
      </Section>
    </div>
  );
}
