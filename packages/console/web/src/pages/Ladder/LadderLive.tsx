import {
  LADDER_HOSPITAL_DAYS,
  type LadderLiveClosedLot,
  type LadderLiveGate,
  type LadderLiveOrder,
  type LadderLivePayload,
  type LadderLiveStatus,
} from "@console/shared";
import { Card, DataCard } from "../../components/DataTable";
import { HelpSection, HelpWarn, type CardHelpContent } from "../../components/CardHelp";
import { Section } from "../../components/Section";
import { TradeTotalsChip } from "../../components/TradeMoney";
import { HistoryTable } from "../../components/table/HistoryTable";
import type { ColumnDef } from "../../components/table/columns";
import { FliesTile } from "../Flies/FliesSession";
import { ageLabel, fmtCash, fmtMoney } from "../../lib/format";
import { MONEY_COLUMNS } from "./LadderPages";
import { pctOfCost, price, targetText, whenEt } from "./ladderModels";

/**
 * The ladder's Live view (issue #1337): the live loop's state and its own ledger, READ ONLY. The gates,
 * the mode, the arm, the halt flag and the standing dry-run intents are the module's own
 * (`live_loop --status`); the lots, orders and journal are `live_trades.db`. The page computes no gate,
 * no fee and no P&L, and has no control: arming is `/live-ladder-start`, and nothing here can arm,
 * disarm, halt or place an order.
 *
 * **LIVE and dry-run are told apart at the top and never merged.** The module ships in dry-run: the
 * loop reads the broker and journals the orders it would send, and sends none until `live.dry_run` is
 * false by hand. A ledger on this page holds real broker records only, so a dry-run machine shows
 * intents and journal rows, never a lot it did not buy. Nothing on this page is paper.
 */

export function modeWords(status: LadderLiveStatus | undefined): { label: string; tone: "live" | "dry" | "unknown"; text: string } {
  if (status === undefined || !status.ok || status.mode === null) {
    return { label: "unknown", tone: "unknown", text: "The module's status could not be read, so whether the loop sends orders is not known. Nothing here is a verdict that it is safe." };
  }
  if (status.mode === "live") {
    return { label: "LIVE", tone: "live", text: "live.dry_run is false: with every gate held, the loop places and cancels real orders on the designated account." };
  }
  return {
    label: "dry-run",
    tone: "dry",
    text: "The loop reads the broker and journals the orders it would send; it sends none until live.dry_run is set to false by hand. Any lot below is a real fill on the account; the intents are not orders.",
  };
}

/** The badge the ladder frame wears on its Live tab: LIVE only on an explicit `live.dry_run: false`. */
export function LiveModeBadge({ status }: { status: LadderLiveStatus | undefined }) {
  const m = modeWords(status);
  return <span className={m.tone === "live" ? "badge badge-live" : "badge badge-dryrun"}>{m.tone === "live" ? "LIVE" : m.tone === "dry" ? "DRY-RUN" : "MODE ?"}</span>;
}

function note(title: string, heading: string, text: string): CardHelpContent {
  return {
    title,
    body: (
      <HelpSection title={heading}>
        <p>{text}</p>
      </HelpSection>
    ),
  };
}

const BLOCKS: Record<LadderLiveGate["blocks"], string> = {
  all: "stops everything",
  new_buys: "stops new buys only",
};

function GateCard({ status }: { status: LadderLiveStatus | undefined }) {
  const gates = status?.gates ?? [];
  return (
    <DataCard
      title="the nine gates"
      className="lv-12"
      headers={["gate", "state", "what a failure stops", "why"]}
      loading={status === undefined}
      isError={status !== undefined && !status.ok}
      rowCount={gates.length}
      empty="the module reported no gate"
      help={note(
        "The gates",
        "How to read it",
        "Each gate is the module's own verdict with its own reason (live_loop --status). Every gate that stops everything must hold for the loop to act on the account; the halt flag stops new buys only, and held lots keep their resting sells. The page decides nothing: it shows what the module says, and the module re-reads the gates on every order.",
      )}
    >
      {gates.map((g) => (
        <tr key={g.name}>
          <td className="mono">{g.name}</td>
          <td>
            <span className={g.ok ? "chip chip-ok" : "chip chip-bad"}>{g.ok ? "holds" : "fails"}</span>
          </td>
          <td className="muted">{BLOCKS[g.blocks]}</td>
          <td className={g.ok ? "muted" : undefined} style={{ textAlign: "left" }}>
            {g.reason || "—"}
          </td>
        </tr>
      ))}
    </DataCard>
  );
}

function StateTiles({ live, status }: { live: LadderLivePayload | undefined; status: LadderLiveStatus | undefined }) {
  const ok = status?.ok === true;
  const held = status?.held;
  const heldCount = held === undefined ? null : held.unresolved.length + held.unrecorded;
  const orphans = live?.orphans.count ?? null;
  const iteration = live?.lastIteration ?? null;
  return (
    <>
      <FliesTile
        title="mode"
        value={status === undefined ? null : modeWords(status).label}
        tone={status?.mode === "live" ? "neg" : "dim"}
        foot={status?.mode === "live" ? "real orders, when every gate holds" : "no order is sent"}
      />
      <FliesTile
        title="ready"
        value={!ok ? null : status.ready ? "ready" : "not ready"}
        tone={!ok ? "dim" : status.ready ? "pos" : "neg"}
        foot={!ok ? "status unavailable" : status.unmetGates.length === 0 ? "every gate holds" : `unmet: ${status.unmetGates.join(", ")}`}
      />
      <FliesTile
        title="armed today"
        value={!ok ? null : status.armedToday ? "armed" : "not armed"}
        tone="dim"
        foot={!ok ? "status unavailable" : status.armedFor === null ? "no arm record" : `arm record for ${status.armedFor}`}
      />
      <FliesTile
        title="halt flag"
        value={!ok ? null : status.haltFlag ? "set" : "clear"}
        tone={!ok ? "dim" : status.haltFlag ? "neg" : "dim"}
        foot="a set flag stops new buys; held lots keep their sells"
      />
      <FliesTile
        title="held submissions"
        value={heldCount === null ? null : String(heldCount)}
        tone={heldCount !== null && heldCount > 0 ? "neg" : "dim"}
        foot={heldCount === null ? "status unavailable" : heldCount === 0 ? "nothing is waiting on a broker answer" : "an uncertain submit is held; the loop refuses what it covers"}
      />
      <FliesTile
        title="orphans"
        value={orphans === null ? null : String(orphans)}
        tone={orphans !== null && orphans > 0 ? "neg" : "dim"}
        foot={live?.orphans.at ? `last sweep ${live.orphans.at}` : "no sweep recorded"}
      />
      <FliesTile
        title="account"
        value={!ok ? null : (status.account ?? "none")}
        tone="dim"
        foot="the designated account, masked"
      />
      <FliesTile
        title="loop"
        value={live === undefined ? null : iteration === null ? "no tick" : iteration.status}
        tone="dim"
        foot={iteration === null ? "no tick recorded in the live ledger" : `last tick ${ageLabel(iteration.ageSeconds)} ago · ${iteration.phase}`}
      />
    </>
  );
}

function ChecksCards({ status }: { status: LadderLiveStatus | undefined }) {
  const checks = status?.checks ?? null;
  const funds = checks?.funds ?? [];
  const intents = checks?.intents ?? [];
  const problems = [
    ...(checks?.splitProblems ?? []).map((p) => `splits: ${p}`),
    ...(checks?.positionsReadable === false ? ["the broker's positions could not be read: no fund bought"] : []),
    ...(checks?.blocked ?? []).map((b) => `${b.fund}: ${b.reason}`),
  ];
  return (
    <>
      <DataCard
        title="positions against the ledger"
        className="lv-6"
        headers={["fund", "ledger shares", "broker shares", "status"]}
        loading={status === undefined}
        isError={status !== undefined && !status.ok}
        rowCount={funds.length}
        numFrom={1}
        empty={checks === null ? "the loop has not ticked yet: no cross-check recorded" : "no fund checked on the last tick"}
        footer={
          checks === null ? undefined : (
            <>
              <span className="muted">last tick {checks.at ?? "—"}</span>
              {(checks.splitsDeclared ?? []).length > 0 && (
                <span className="muted"> · declared splits: {checks.splitsDeclared.map((s) => `${s.fund} ${s.ratio} ex ${s.exDate}`).join(", ")}</span>
              )}
            </>
          )
        }
        help={note(
          "Positions against the ledger",
          "How to read it",
          "The broker's equity shares in each fund against the shares the live ledger holds. Fewer at the broker than in the ledger is a shortfall, and more is a surplus (shares the account holds by hand, never adopted). A shortfall or a ratio that looks like a split stops that fund's new buys. The figures are the last tick's, read from the module's own record.",
        )}
      >
        {funds.map((f) => (
          <tr key={f.fund}>
            <td>{f.fund}</td>
            <td>{f.ledger ?? "—"}</td>
            <td>{f.broker ?? "—"}</td>
            <td className={f.status === "match" ? "muted" : f.status === "surplus" ? undefined : "pnl-neg"}>
              {f.status ?? "—"}
              {f.suspectedSplit !== null ? ` · looks like a ${f.suspectedSplit} split` : ""}
            </td>
          </tr>
        ))}
      </DataCard>
      <Card title="what is stopping buys" className="lv-6" collapseKey="ladder-live-problems">
        {problems.length === 0 ? (
          <p className="muted">{checks === null ? "No tick has recorded its checks yet." : "Nothing on the last tick stopped a fund's buys."}</p>
        ) : (
          <ul className="pmcc-rules">
            {problems.map((p) => (
              <li key={p} className="pnl-neg">
                {p}
              </li>
            ))}
          </ul>
        )}
      </Card>
      <DataCard
        title="standing dry-run intents"
        className="lv-12"
        headers={["order the loop would send", "lot", "shares", "limit", "tif", "why"]}
        loading={status === undefined}
        isError={status !== undefined && !status.ok}
        rowCount={intents.length}
        numFrom={2}
        empty={status?.mode === "live" ? "the loop is live: it sends its orders, it lists no intent" : "no intent standing on the last tick"}
        help={note(
          "Dry-run intents",
          "How to read it",
          "While live.dry_run is true the loop sends nothing: each order it would send is listed here, from its last tick, and journaled once when its terms change. These are not orders and none rests at the broker.",
        )}
      >
        {intents.map((i) => (
          <tr key={i.what}>
            <td className="mono">{i.what}</td>
            <td className="muted">{i.lot ?? "—"}</td>
            <td>{i.shares ?? "—"}</td>
            <td>{price(i.limit)}</td>
            <td className="muted">{i.tif ?? "—"}</td>
            <td className="muted" style={{ textAlign: "left" }}>
              {i.reason ?? "—"}
            </td>
          </tr>
        ))}
      </DataCard>
    </>
  );
}

function feeSource(s: string | null): string {
  return s === "broker" ? "broker's own rows" : s === "modeled" ? "modeled (the schedule)" : "not recorded";
}

function OpenLots({ live }: { live: LadderLivePayload | undefined }) {
  const lots = live?.openLots ?? [];
  return (
    <DataCard
      title="open live lots"
      className="lv-12"
      headers={["fund", "lot", "shares × price", "cost", "target", "held", "sell at the broker", "fees so far", "account"]}
      loading={live === undefined}
      isError={live?.ledger === "failed"}
      rowCount={lots.length}
      numFrom={2}
      empty={live?.ledger === "absent" ? "no live ledger on this machine" : "no live lot is held"}
      help={{
        title: "Open live lots",
        body: (
          <>
            <HelpSection title="Reading a row">
              <p>
                A lot is shares the loop bought at a resting limit and is waiting to sell at its target. Cost is shares times the price paid, whole position and negative
                (a debit). The sell is the order resting at the broker for this lot, with its state, limit and time in force; a lot with none shows a dash. Fees so far are the
                buy&apos;s, from the schedule until the broker&apos;s own rows are read. There is no mark on a live lot here and no stop.
              </p>
            </HelpSection>
            <HelpWarn title={`Past ${String(LADDER_HOSPITAL_DAYS)} days`}>
              <p>
                A lot held more than {String(LADDER_HOSPITAL_DAYS)} days is flagged. The live loop alerts and never acts on it automatically.
              </p>
            </HelpWarn>
          </>
        ),
      }}
    >
      {lots.map((l) => (
        <tr key={l.positionId}>
          <td>{l.symbol}</td>
          <td className="mono" title={`${l.arm} arm · bought ${whenEt(l.buySession, l.buyTime)} ET`}>
            {l.positionId}
          </td>
          <td>
            {String(l.quantity)} × {price(l.buyPrice)}
          </td>
          <td>{fmtCash(l.entryValue)}</td>
          <td>{targetText(l)}</td>
          <td>
            {String(l.daysHeld)} {l.daysHeld === 1 ? "day" : "days"}
            {l.hospital ? (
              <>
                {" "}
                <span className="chip chip-warn" title={`held more than ${String(LADDER_HOSPITAL_DAYS)} days`}>
                  past {String(LADDER_HOSPITAL_DAYS)} d
                </span>
              </>
            ) : null}
          </td>
          <td>{l.sell === null ? "—" : `${l.sell.state}${l.sell.final ? " (ended)" : ""} · ${price(l.sell.limit)}${l.sell.tif ? ` · ${l.sell.tif}` : ""}`}</td>
          <td title={feeSource(l.feesSource)}>{l.buyFees === null ? "n/r" : fmtMoney(l.buyFees)}</td>
          <td className="muted">{l.account ?? "—"}</td>
        </tr>
      ))}
    </DataCard>
  );
}

const FEES_FROM: ColumnDef<LadderLiveClosedLot> = {
  id: "feesfrom",
  header: "fees from",
  title: "where the fees came from: modeled is the schedule's, broker is the broker's own transaction rows",
  kind: "describe",
  className: "muted",
  render: (r) => (r.feesSource === null ? "n/r" : r.feesSource),
  sortValue: (r) => r.feesSource,
};

const CLOSED_DEFS: ColumnDef<LadderLiveClosedLot>[] = [
  { id: "symbol", header: "fund", kind: "describe", render: (r) => r.symbol, sortValue: (r) => r.symbol },
  { id: "bought", header: "bought (ET)", kind: "describe", render: (r) => whenEt(r.buySession, r.buyTime), sortValue: (r) => r.buyTime },
  { id: "sold", header: "sold (ET)", kind: "describe", render: (r) => whenEt(r.sellSession, r.sellTime), sortValue: (r) => r.sellTime },
  { id: "qty", header: "shares", kind: "describe", numeric: true, render: (r) => r.quantity ?? "—", sortValue: (r) => r.quantity },
  {
    id: "prices",
    header: "buy → sell",
    title: "per share: the price paid, and the price sold at (the fills, not the limits)",
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
  FEES_FROM,
  { id: "account", header: "account", kind: "describe", className: "muted", render: (r) => r.account ?? "—", sortValue: (r) => r.account },
  ...MONEY_COLUMNS,
];

function ClosedLots({ live }: { live: LadderLivePayload | undefined }) {
  const rows = live?.closedLots ?? [];
  const src = live?.feesBySource;
  return (
    <HistoryTable
      table="ladder-live-closed"
      title="sold live lots"
      className="view-fade lv-12"
      defs={CLOSED_DEFS}
      clientSort
      dateRange={false}
      rows={rows}
      rowKey={(r) => r.positionId}
      loading={live === undefined}
      isError={live?.ledger === "failed"}
      empty={live?.ledger === "absent" ? "no live ledger on this machine" : "no live lot has sold yet"}
      footer={
        live !== undefined && (
          <>
            <TradeTotalsChip totals={live.closedTotals} noun="lots" />
            {src !== undefined && src.modeled + src.broker + src.unrecorded > 0 && (
              <span className="muted">
                {" "}
                · fees: {String(src.modeled)} modeled, {String(src.broker)} from the broker
                {src.unrecorded > 0 ? `, ${String(src.unrecorded)} not recorded` : ""}
              </span>
            )}
            {live.closedLots.length < live.closedTotals.positions && (
              <span className="muted"> · the newest {String(live.closedLots.length)} rows; the totals cover every sold lot</span>
            )}
          </>
        )
      }
      help={note(
        "Sold live lots",
        "Reading a row",
        "Entry is the purchase (negative) and exit the sale (positive), at the broker's fills; they add up to gross. Gross less fees, settlement and slippage is net. Fees are the buy's and the sale's together, with their source beside them: modeled is the tastytrade schedule, broker is the broker's own transaction rows. Settlement and slippage are zero for shares filled at a resting limit.",
      )}
    />
  );
}

function orderWord(o: LadderLiveOrder): string {
  return o.filledShares !== null && o.filledShares > 0 ? `${String(o.filledShares)} filled${o.fillPrice !== null ? ` @ ${price(o.fillPrice)}` : ""}` : "—";
}

function Orders({ live }: { live: LadderLivePayload | undefined }) {
  const orders = live?.orders ?? [];
  return (
    <DataCard
      title="orders"
      className="lv-12"
      headers={["side", "fund", "shares", "limit", "tif", "state", "fill", "fees", "placed", "account"]}
      loading={live === undefined}
      isError={live?.ledger === "failed"}
      rowCount={orders.length}
      numFrom={2}
      empty={live?.ledger === "absent" ? "no live ledger on this machine" : "no order recorded"}
      help={note(
        "Orders",
        "How to read it",
        "Every order the live loop placed or found at the broker (one with a ladder client id the ledger had not recorded is marked adopted), working ones first and then the most recent that ended. State is the broker's. A resting sell on every held lot is the design: a disarm, an outage or a crash never leaves a lot without its exit.",
      )}
    >
      {orders.map((o) => (
        <tr key={o.clientId} className={o.final ? "muted" : undefined}>
          <td className={o.side === "buy" ? undefined : "pnl-pos"}>{o.side}</td>
          <td title={o.lotId ?? undefined}>{o.symbol}</td>
          <td>{o.shares}</td>
          <td>{price(o.limit)}</td>
          <td className="muted">{o.tif ?? "—"}</td>
          <td>
            {o.state}
            {o.adopted ? " · adopted" : ""}
          </td>
          <td>{orderWord(o)}</td>
          <td title={feeSource(o.feesSource)}>{o.fees === null ? "—" : fmtMoney(o.fees)}</td>
          <td className="muted">{o.placedSession ?? "—"}</td>
          <td className="muted">{o.account ?? "—"}</td>
        </tr>
      ))}
    </DataCard>
  );
}

const MODE_CHIP: Record<string, string> = {
  alert: "chip chip-bad",
  refused: "chip chip-bad",
  blocked: "chip chip-warn",
  dry_run: "chip chip-hold",
  split: "chip chip-ok",
  positions: "chip chip-hold",
  order: "chip chip-ok",
};

function Journal({ live }: { live: LadderLivePayload | undefined }) {
  const rows = live?.journal ?? [];
  return (
    <DataCard
      title="journal"
      className="lv-12"
      headers={["day", "kind", "fund", "what", "times"]}
      loading={live === undefined}
      isError={live?.ledger === "failed"}
      rowCount={rows.length}
      empty={live?.ledger === "absent" ? "no live ledger on this machine" : "the journal is empty"}
      help={note(
        "Journal",
        "How to read it",
        "The live loop's own decision journal, newest first: alerts (a lot past its days, a partial fill), refusals (an order the broker or a gate turned down), buys it blocked and why, splits applied, and the dry-run intents, one row until their terms change. Accounts are masked.",
      )}
    >
      {rows.map((r) => (
        <tr key={r.id} title={r.detail ?? undefined}>
          <td className="muted">{r.tradeDate}</td>
          <td>
            <span className={MODE_CHIP[r.mode] ?? "chip"}>{r.mode.replace("_", "-")}</span>
          </td>
          <td>{r.symbol}</td>
          <td style={{ textAlign: "left" }}>{r.reason}</td>
          <td className="muted">{r.occurrences > 1 ? `×${String(r.occurrences)}` : ""}</td>
        </tr>
      ))}
    </DataCard>
  );
}

export function LadderLive({ live, status }: { live: LadderLivePayload | undefined; status: LadderLiveStatus | undefined }) {
  const mode = modeWords(status);
  return (
    <div className="pf-layout">
      <p className="ts-muted ts-status" role="note">
        <strong>{mode.tone === "live" ? "Live." : mode.tone === "dry" ? "Dry-run, not live." : "Mode unknown."}</strong> {mode.text} This page only reads: arming is
        the <span className="mono">/live-ladder-start</span> command, and nothing here can arm, disarm, halt or place an order.
      </p>
      {status !== undefined && !status.ok && <p className="integrity-warn">{status.error}</p>}
      {live?.ledger === "failed" && <p className="integrity-warn">The live ledger could not be read: {live.ledgerError ?? "unknown error"}</p>}
      <Section concept="stand" tag="the loop" question="Is the live loop ready, and is it sending orders?">
        <StateTiles live={live} status={status} />
      </Section>
      <Section concept="add" tag="the gates" question="Which gates hold?">
        <GateCard status={status} />
      </Section>
      <Section concept="add" tag="the checks" question="What did the last tick find?">
        <ChecksCards status={status} />
      </Section>
      {live?.ledger === "absent" ? (
        <Section concept="stand" tag="not started" question="Has the live loop traded on this machine?">
          <Card title="no live ledger" collapseKey="ladder-live-absent" className="lv-12">
            <p className="muted">
              The live loop has not run here: there is no live store at <span className="mono">~/.cherrypick/data/ladder/live_trades.db</span> yet. It appears when the live
              loop first ticks. Until then the gates and the checks above are the module&apos;s own readout, and there are no lots, orders or journal rows to show.
            </p>
          </Card>
        </Section>
      ) : (
        <>
          <Section concept="stand" tag="the book" question="Which live lots are held?">
            <OpenLots live={live} />
          </Section>
          <Section concept="hist" tag="the record" question="How did each sold live lot end?">
            <ClosedLots live={live} />
          </Section>
          <Section concept="add" tag="the orders" question="What is resting at the broker?">
            <Orders live={live} />
          </Section>
          <Section concept="dir" tag="the journal" question="What did the loop decide, refuse or alert?">
            <Journal live={live} />
          </Section>
        </>
      )}
    </div>
  );
}
