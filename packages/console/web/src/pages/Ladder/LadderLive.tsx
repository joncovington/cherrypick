import {
  LADDER_HOSPITAL_DAYS,
  type LadderLiveGate,
  type LadderLiveOrder,
  type LadderLivePayload,
  type LadderLiveStatus,
} from "@console/shared";
import { Link } from "react-router-dom";
import { Card, DataCard } from "../../components/DataTable";
import { HelpSection, HelpWarn, type CardHelpContent } from "../../components/CardHelp";
import { Section } from "../../components/Section";
import { FliesTile } from "../Flies/FliesSession";
import { ageLabel, fmtCash, fmtMoney } from "../../lib/format";
import { price, targetText, whenEt } from "./ladderModels";

/**
 * The ladder's pages under the frame's Live mode (issue #1337): the live loop's state and its own
 * ledger, READ ONLY. The gates, the mode, the arm, the halt flag and the standing dry-run intents are
 * the module's own (`live_loop --status`); the lots, orders and journal are `live_trades.db`. Three
 * tabs have a live read (today, open lots, history); every other tab says so in one line. The page
 * computes no gate, no fee and no P&L, and has no control: arming stays on the suite-wide Live page,
 * and nothing here can arm, disarm, halt or place an order.
 *
 * **LIVE and dry-run are told apart at the top and never merged.** The module ships in dry-run: the
 * loop reads the broker and journals the orders it would send, and sends none until `live.dry_run` is
 * false by hand. A ledger on this page holds real broker records only, so a dry-run machine shows
 * intents and journal rows, never a lot it did not buy. Nothing on these pages is paper, and a card
 * with nothing to show is left out rather than drawn empty.
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

/** What the last tick's checks have to show: each card is drawn only when it has rows. */
function checksShown(status: LadderLiveStatus | undefined) {
  const checks = status?.checks ?? null;
  const funds = checks?.funds ?? [];
  const intents = checks?.intents ?? [];
  const problems = [
    ...(checks?.splitProblems ?? []).map((p) => `splits: ${p}`),
    ...(checks?.positionsReadable === false ? ["the broker's positions could not be read: no fund bought"] : []),
    ...(checks?.blocked ?? []).map((b) => `${b.fund}: ${b.reason}`),
  ];
  return { checks, funds, intents, problems };
}

function ChecksCards({ status }: { status: LadderLiveStatus | undefined }) {
  const { checks, funds, intents, problems } = checksShown(status);
  return (
    <>
      {funds.length > 0 && (
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
      )}
      {problems.length > 0 && (
        <Card title="what is stopping buys" className="lv-6" collapseKey="ladder-live-problems">
          <ul className="pmcc-rules">
            {problems.map((p) => (
              <li key={p} className="pnl-neg">
                {p}
              </li>
            ))}
          </ul>
        </Card>
      )}
      {intents.length > 0 && (
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
      )}
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
      headers={["arm", "fund", "bought (ET)", "lot", "cost", "target", "held", "sell at the broker", "fees so far"]}
      loading={live === undefined}
      isError={live?.ledger === "failed"}
      rowCount={lots.length}
      numFrom={3}
      empty={ledgerLine(live, "No live lot is held.")}
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
        <tr key={l.positionId} title={l.account === null ? undefined : `account ${l.account}`}>
          <td>{l.arm}</td>
          <td>{l.symbol}</td>
          <td>{whenEt(l.buySession, l.buyTime)}</td>
          <td className="mono" title={l.positionId}>
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
        </tr>
      ))}
    </DataCard>
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
      className="lv-6"
      headers={["side", "fund", "shares", "limit", "tif", "state", "fill", "fees", "placed", "account"]}
      loading={live === undefined}
      isError={live?.ledger === "failed"}
      rowCount={orders.length}
      numFrom={2}
      empty="no order recorded"
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
      className="lv-6"
      headers={["day", "kind", "fund", "what", "times"]}
      loading={live === undefined}
      isError={live?.ledger === "failed"}
      rowCount={rows.length}
      empty="the journal is empty"
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

/** What every Live tab opens with: whether the loop sends orders (told apart from live, never merged), and a ledger or status that could not be read. */
export function LiveLedgerNotes({ live, status }: { live: LadderLivePayload | undefined; status: LadderLiveStatus | undefined }) {
  const mode = modeWords(status);
  return (
    <>
      <p className="ts-muted ts-status" role="note">
        <strong>{mode.tone === "live" ? "Live." : mode.tone === "dry" ? "Dry-run, not live." : "Mode unknown."}</strong> {mode.text}
      </p>
      {status !== undefined && !status.ok && <p className="integrity-warn">{status.error}</p>}
      {live?.ledger === "failed" && <p className="integrity-warn">The live ledger could not be read: {live.ledgerError ?? "unknown error"}</p>}
    </>
  );
}

/** The one line a Live tab shows in place of a table with nothing in it. */
export function ledgerLine(live: LadderLivePayload | undefined, whenEmpty: string): string {
  return live?.ledger === "absent" ? "No live ledger on this machine yet." : whenEmpty;
}

/** The open lots tab under Live: the live book's held lots, with no bid or value (the live ledger keeps no mark on a held lot). */
export function LadderLiveLots({ live, status }: { live: LadderLivePayload | undefined; status: LadderLiveStatus | undefined }) {
  const notes = <LiveLedgerNotes live={live} status={status} />;
  if (live !== undefined && live.openLots.length === 0) {
    return (
      <div className="pf-layout">
        {notes}
        <p className="muted" role="note">
          {ledgerLine(live, "No live lot is held.")}
        </p>
      </div>
    );
  }
  return (
    <div className="pf-layout">
      {notes}
      <Section concept="stand" tag="the book" question="Which live lots are held?">
        <OpenLots live={live} />
      </Section>
    </div>
  );
}

/** Any tab with no live read yet: one line and a way back to today, never the paper book's figures under a live label. */
export function LadderNoLiveRead() {
  return (
    <div className="pf-layout">
      <p className="muted" role="note">
        No live read for this tab. <Link to="/ladder/summary?mode=live">Back to today</Link>
      </p>
    </div>
  );
}

/** The today tab under Live: the live loop's own cards. Read only: arming stays on the Live page. */
export function LadderLiveToday({ live, status }: { live: LadderLivePayload | undefined; status: LadderLiveStatus | undefined }) {
  const checks = status?.checks ?? null;
  const gates = status?.gates ?? [];
  const showJournal = live === undefined || (live.ledger !== "absent" && live.journal.length > 0);
  const showOrders = live === undefined || (live.ledger !== "absent" && live.orders.length > 0);
  return (
    <div className="pf-layout">
      <LiveLedgerNotes live={live} status={status} />
      <Section concept="stand" tag="the loop" question="Is the live loop ready, and is it sending orders?">
        <StateTiles live={live} status={status} />
      </Section>
      <p className="ts-muted ts-status" role="note">
        Arm it for today from the Live page. Nothing here arms.
      </p>
      {(status === undefined || gates.length > 0) && (
        <Section concept="add" tag="the gates" question="Which gates hold?">
          <GateCard status={status} />
        </Section>
      )}
      {(status === undefined || checks !== null) && (
        <Section concept="add" tag="the checks" question="What did the last tick find?">
          <ChecksCards status={status} />
        </Section>
      )}
      {(showJournal || showOrders) && (
        <Section concept="dir" tag="the journal" question="What did the loop decide, refuse or alert, and what is resting at the broker?">
          {showJournal && <Journal live={live} />}
          {showOrders && <Orders live={live} />}
        </Section>
      )}
    </div>
  );
}
