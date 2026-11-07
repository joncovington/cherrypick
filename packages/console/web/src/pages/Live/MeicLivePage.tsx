import { useQuery } from "@tanstack/react-query";
import type { LiveMeicOpenRow, LiveMeicPayload } from "@console/shared";
import { Card, DataCard, PnlCell, fmtMoney } from "../../components/DataTable";
import { HelpSection, type CardHelpContent } from "../../components/CardHelp";
import { Cells, headersOf, useColumns, type Column } from "../../components/columns";
import { HistoryTable } from "../../components/table/HistoryTable";
import { ListViewContext, ViewSwitch, useViewChoice } from "../../components/ViewSwitch";
import { Tile } from "../Flies/PerformanceTab";
import { HISTORY_COLUMNS, feesNetWords } from "../Meic/MeicTables";
import { ExecutionPanel } from "./LiveExecution";
import { LiveBoxes, StateLine } from "./liveParts";
import { MeicLiveGapCard, MeicLivePairs, useMeicLiveGap } from "./MeicLiveGap";
import { MeicReadinessCard } from "./MeicReadinessCard";
import type { Box } from "./PortfolioBox";
import { Section } from "./PortfolioLayout";
import { TradeCards, type TradeCardModel } from "./TradeCard";

/**
 * MEIC's live loop, read-only (2026-10-15): the flies Live tab's arming strip and trade tables, cut
 * to what MEIC's live loop records. It keeps no per-tick marks and no decision journal, so there is
 * no intraday curve and no activity feed; the loop's own `--status` keys the watchdog reads
 * (armed_for, orphaned orders, held submissions, broker health) are read here from the same files.
 * There is no button here that touches an order.
 *
 * Money is the MEIC page's: the same server derivation and the same history columns, less the
 * slippage column -- a live fill is the broker's price, and MEIC's slippage figure is a paper
 * model's concession.
 */
export function useLiveMeic() {
  return useQuery<LiveMeicPayload>({
    queryKey: ["live-meic"],
    queryFn: async () => {
      const res = await fetch("/api/live/meic");
      if (!res.ok) throw new Error(`live meic: HTTP ${res.status}`);
      return (await res.json()) as LiveMeicPayload;
    },
    refetchInterval: 15_000,
  });
}

const LIVE_HISTORY_COLUMNS = HISTORY_COLUMNS.filter((c) => c.id !== "slip");

function hhmmOf(ts: string | null): string {
  return ts !== null && ts.length >= 16 ? ts.slice(11, 16) : "—";
}

function sides(r: LiveMeicOpenRow): string {
  if (r.working) return "—";
  const side = (name: string, held: boolean, closing: boolean) => (closing ? `${name} closing` : held ? name : null);
  const held = [side("put", r.putHeld, r.putClosing), side("call", r.callHeld, r.callClosing)].filter((s) => s !== null);
  return held.length === 0 ? "none" : held.join(" + ");
}

function state(r: LiveMeicOpenRow): string {
  if (r.working) return "entry working";
  return r.overdue ? `${r.status} · overdue` : r.status;
}

/** One row of the orphaned and held orders: an orphan, an unresolved submission or an unrecorded fill. */
export interface AttentionRow {
  key: string;
  kind: string;
  order: string;
  status: string;
  quietStatus: boolean;
  symbol: string;
  ext: string;
}

/** The external id is the detail view's: the order id and status are what a person acts on. */
export const ORDER_COLUMNS: Column<AttentionRow>[] = [
  { key: "kind", label: "kind", td: (r) => <td className="pnl-neg">{r.kind}</td> },
  { key: "order", label: "order", td: (r) => <td>{r.order}</td> },
  { key: "status", label: "status", td: (r) => <td className={r.quietStatus ? "muted" : undefined}>{r.status}</td> },
  { key: "symbol", label: "symbol", td: (r) => <td>{r.symbol}</td> },
  { key: "ext", label: "external id", detailOnly: true, td: (r) => <td className="muted">{r.ext}</td> },
];

function armLabel(d: LiveMeicPayload): { value: string; tone?: "pos" | "neg" | "dim" } {
  const a = d.arm;
  if (a.armed && a.pastDisarm) return { value: "armed · past disarm", tone: "dim" };
  if (a.armed) return { value: "ARMED", tone: "pos" };
  if (a.stale) return { value: "stale arm", tone: "dim" };
  return { value: "not armed", tone: "dim" };
}

/**
 * The readiness block's line for a held condor, e.g. `LIVE-SPX-14 SPX 7565p/7615c 10w, credit 1.8,
 * holds call, stop 2.0x credit`, is the module's own; it names the condor by its short strikes.
 */
export function icLineFor(r: LiveMeicOpenRow, lines: readonly string[]): string | null {
  if (r.putStrike === null || r.callStrike === null) return null;
  const key = `${r.putStrike}p/${r.callStrike}c`;
  return lines.find((l) => l.includes(key)) ?? null;
}

/** The stop the module states for a held condor (`stop 2.0x credit`), or null where the line names none. */
export function stopWords(line: string | null): string | null {
  const m = line === null ? null : /(?:^|,\s*)(stop [^,]+)/.exec(line);
  return m?.[1]?.replace(/(\d)x credit/, "$1× credit") ?? null;
}

/**
 * An open condor as a trade card, in the order-chain look: its four legs, the credit it took in, the
 * state and the sides still held. The stop is said once, in the card's foot, from the module's own line
 * (`ics`). The live read carries no mark, so the card has no bar for the credit kept.
 */
export function openModel(r: LiveMeicOpenRow, ics: readonly string[] = []): TradeCardModel {
  const clock = hhmmOf(r.entryTime);
  const wing = r.wingWidth;
  const q = r.quantity ?? 1;
  const leg = (buy: boolean, strike: number | null, right: "P" | "C") => ({ buy, text: `${q} ${strike ?? "?"}${right}`, quote: null });
  const legs = [
    leg(false, r.putStrike, "P"),
    ...(wing !== null && r.putStrike !== null ? [leg(true, r.putStrike - wing, "P")] : []),
    leg(false, r.callStrike, "C"),
    ...(wing !== null && r.callStrike !== null ? [leg(true, r.callStrike + wing, "C")] : []),
  ];
  const credit = r.netCredit === null ? "—" : `${r.netCredit.toFixed(2)} ${r.working ? "limit" : "cr"}`;
  const stop = stopWords(icLineFor(r, ics));
  return {
    key: String(r.id),
    symbol: `IC ${clock}`,
    spot: null,
    shape: `${r.symbol} ${r.putStrike ?? "?"}/${r.callStrike ?? "?"}${wing === null ? "" : ` · ${wing} wide`}${q > 1 ? ` · ×${q}` : ""}`,
    chips: <span className={`chip ${r.overdue ? "chip-bad" : r.working ? "" : "chip-ok"}`}>{state(r)}</span>,
    cash: r.entryCash,
    side: [credit, r.tradeDate],
    bar: null,
    hideBar: true,
    barTitle: "",
    label: r.working ? "entry working at the broker" : `sides held: ${sides(r)}`,
    open: { date: clock, price: credit },
    legs,
    after: [r.working ? "no fill yet" : r.fillConfirmedAt !== null ? `fill confirmed ${hhmmOf(r.fillConfirmedAt)}` : "fill not confirmed"],
    foot: [r.arm === null ? "live" : `arm ${r.arm}`, stop ?? "no stop stated"],
  };
}

const OPEN_HELP: CardHelpContent = {
  title: "Open condors",
  body: (
    <>
      <HelpSection title="A condor">
        <p>Each live condor: its short strikes, wing, quantity, entry, state and which sides are still held. The card&apos;s foot says the stop, as the module states it.</p>
      </HelpSection>
      <HelpSection title="What the card does not have">
        <p>The live loop keeps no per-tick marks, so there is no mark and no credit-kept bar here; a side closed by its stop is said in the state.</p>
      </HelpSection>
    </>
  ),
};

const HISTORY_HELP: CardHelpContent = {
  title: "History",
  body: (
    <HelpSection title="Net">
      <p>Gross less fees less settlement. A settlement not recorded reads n/r, and a trade that never recorded its fees has no known net.</p>
    </HelpSection>
  ),
};

export function MeicLivePage() {
  const { data: d, isLoading, isError, dataUpdatedAt } = useLiveMeic();
  const gap = useMeicLiveGap();
  const arm = d !== undefined ? armLabel(d) : null;
  const health = d?.brokerHealth ?? null;
  const heldCount = d !== undefined ? d.held.unresolved.length + d.held.unrecorded.length : 0;
  const t = d?.totals;
  const orderCols = useColumns(ORDER_COLUMNS);
  // Open condors default to rows: a day holds several, and a row opens its card in place.
  const [view, setView] = useViewChoice("live-meic-open", "rows");
  const ics = d?.readiness?.ok ? d.readiness.openIcs : [];
  const shownIcs = (ic: string) => (d?.open ?? []).some((r) => icLineFor(r, [ic]) !== null);
  const boxes: Box[] = [
    {
      id: "execution",
      kicker: "live vs paper ›",
      title: "Execution by week",
      tabs: [{ id: "weeks", label: "by week" }],
      body: () => <ExecutionPanel module="meic" />,
    },
    ...(gap.data && gap.data.rows.length > 0
      ? [
          {
            id: "pairs",
            kicker: "live vs paper ›",
            title: `Pair by pair — ${gap.data.twin}`,
            tabs: [{ id: "pairs", label: "pairs" }],
            body: () => <MeicLivePairs d={gap.data!} />,
          },
        ]
      : []),
  ];

  const tiles = d && (
    <div className="pf-tiles pf-tiles-6">
      <Tile
        label={`arm · ${d.session}`}
        value={arm?.value ?? "—"}
        tone={arm?.tone}
        title={
          `arm record ${d.arm.date ?? "none"}${d.arm.at !== null ? ` (written ${d.arm.at})` : ""}; armed means dated today. ` +
          `The loop's next tick deletes a record that is stale or past the disarm time (${d.arm.disarmTime} ET).`
        }
      />
      <Tile label="disarm time" value={`${d.arm.disarmTime} ET`} title="live.disarm_time in MEIC's config, else the loop's default 17:00" />
      <Tile
        label="halt flag"
        value={d.arm.halted ? "HALTED" : "clear"}
        tone={d.arm.halted ? "neg" : "dim"}
        title="state/halt-live.flag: blocks new ICs only; fills are still confirmed and open ICs still stopped and closed. Clearing it arms nothing."
      />
      <Tile
        label="live gate · symbol"
        value={`${d.arm.liveEnabled === null ? "unknown" : d.arm.liveEnabled ? "on" : "off"} · ${d.arm.symbol ?? "?"}`}
        title="enable_live_trading and live.symbol from MEIC's config; unknown means the config could not be read, never off"
      />
      <Tile
        label="orphaned orders"
        value={String(d.orphans.orders.length)}
        tone={d.orphans.orders.length > 0 ? "neg" : "dim"}
        title={
          d.orphans.at !== null ? `working broker orders no live ledger knows, as of the last sweep at ${d.orphans.at}` : "no sweep recorded yet"
        }
      />
      <Tile
        label="held · broker"
        value={`${heldCount} held · ${health === null ? "no record" : health.failingSince !== null ? `failing ×${health.failures}` : "ok"}`}
        tone={heldCount > 0 || (health?.failingSince ?? null) !== null ? "neg" : "dim"}
        title={
          `held: submissions the broker never answered, or fills the ledger never recorded. ` +
          (health === null
            ? "No broker-contact record yet."
            : `Broker last ok ${health.lastOkAt ?? "never"}` +
              (health.failingSince !== null ? `; failing since ${health.failingSince}: ${health.lastError ?? ""}` : ""))
        }
      />
    </div>
  );

  return (
    <LiveBoxes boxes={boxes}>
      <div className="pf-layout">
        <Section concept="live" question="Can it trade today?">
          <MeicReadinessCard readiness={d?.readiness} tiles={tiles} shown={shownIcs} />
          {d && (d.orphans.orders.length > 0 || heldCount > 0) && (
            <DataCard
              title="orphaned and held orders"
              className="lv-12"
              headers={headersOf(orderCols)}
              loading={false}
              isError={isError}
              rowCount={d.orphans.orders.length + heldCount}
            >
              {[
                ...d.orphans.orders.map(
                  (o, i): AttentionRow => ({
                    key: `o-${i}`,
                    kind: "orphan",
                    order: o.orderId ?? "—",
                    status: o.status ?? "—",
                    quietStatus: false,
                    symbol: o.underlyingSymbol ?? "—",
                    ext: o.externalIdentifier ?? "—",
                  }),
                ),
                ...d.held.unresolved.map(
                  (ext): AttentionRow => ({ key: `u-${ext}`, kind: "unresolved", order: "—", status: "no broker answer", quietStatus: true, symbol: "—", ext }),
                ),
                ...d.held.unrecorded.map(
                  (u): AttentionRow => ({
                    key: `r-${u.ext}`,
                    kind: "unrecorded",
                    order: String(u.orderId),
                    status: "filled, not in the ledger",
                    quietStatus: true,
                    symbol: "—",
                    ext: u.ext,
                  }),
                ),
              ].map((r) => (
                <tr key={r.key}>
                  <Cells cols={orderCols} row={r} />
                </tr>
              ))}
            </DataCard>
          )}
        </Section>
        {d && d.ledger !== "ok" && (
          <StateLine tone={d.ledger === "failed" ? "bad" : "note"}>
            {d.ledger === "absent"
              ? "no MEIC live ledger on this machine yet — the live loop has not traded here"
              : `live ledger read failed: ${d.ledgerError ?? "unknown"}`}
          </StateLine>
        )}

        <Section concept="dir" question="What is open?">
          <Card
            title={`open iron condors${d ? ` · ${d.open.length}` : ""}`}
            collapseKey="live-meic-open"
            updatedAt={dataUpdatedAt}
            isError={isError}
            help={OPEN_HELP}
            controls={<ViewSwitch view={view} onChange={setView} />}
          >
            {isLoading ? (
              <p className="muted">loading</p>
            ) : (
              <ListViewContext.Provider value={view}>
                <TradeCards models={(d?.open ?? []).map((r) => openModel(r, ics))} empty="no open live iron condors" />
              </ListViewContext.Provider>
            )}
          </Card>
        </Section>

        <Section concept="hist" question="How has it done?">
          <HistoryTable
            table="meic-live-history"
            title={`History — ${d?.history.length ?? 0} resolved live`}
            className="lv-8"
            defs={LIVE_HISTORY_COLUMNS}
            clientSort
            rows={d?.history ?? []}
            rowKey={(r) => `live-${r.id}`}
            loading={isLoading}
            isError={isError}
            empty="no resolved live iron condors"
            updatedAt={dataUpdatedAt}
            dateRange={false}
            footer={
              t !== undefined &&
              t.trades > 0 && (
                <span
                  className="chip"
                  title={
                    "Over every resolved live row. Gross − fees − settlement = net. " +
                    (t.net === null
                      ? `Fees were recorded on ${t.feesTrades} of these ${t.trades} trades; a trade that never recorded them has no known net, so the totals' fees and net are n/r. `
                      : "") +
                    (t.settlementFees === null
                      ? `Settlement was recorded on ${t.settlementTrades} of these ${t.trades} trades (${fmtMoney(t.settlementRecorded)}); the rest is inside fees.`
                      : "")
                  }
                >
                  net {feesNetWords(t).net ?? <PnlCell v={t.net} />} · {t.trades} trades · {t.sessions} session{t.sessions === 1 ? "" : "s"} · gross{" "}
                  {fmtMoney(t.gross)} · fees {feesNetWords(t).fees} · settlement{" "}
                  {t.settlementFees === null ? `n/r (recorded on ${t.settlementTrades} of ${t.trades})` : fmtMoney(t.settlementFees)}
                </span>
              )
            }
          />
          <MeicLiveGapCard className="lv-4" />
        </Section>
      </div>
    </LiveBoxes>
  );
}
