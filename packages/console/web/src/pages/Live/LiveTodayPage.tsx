import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import type { ArmAttempt, LiveArmingState, LiveLoopSwitch, LiveTodayLoop, LiveTodayPair, LiveTodayPayload, ManagePayload } from "@console/shared";
import { latestAttempt, useLiveArming } from "../../lib/liveArming";
import { ArmStrip } from "./ArmSwitch";
import { Card, PnlCell, fmtNum } from "../../components/DataTable";
import { HelpSection, HelpWarn, type CardHelpContent } from "../../components/CardHelp";
import { type Column } from "../../components/columns";
import { fmtMoney } from "../../lib/format";
import { etClock } from "../../lib/etTime";
import { useLiveToday } from "../../lib/useLiveToday";
import { Tile } from "../Flies/PerformanceTab";
import { CardLightbox, LightboxFrame, useOpenParam, type LightboxTab } from "../Backtest/CardLightbox";
import { useManage } from "./ManagePage";
import { tone } from "./manageParts";
import { BrokerSummaryCard } from "./WhatToDo";
import { AttentionChip, kindWords } from "./AttentionChip";
import { HandCard, handHasContent, handHasTrades } from "./HandToday";
import { LoopOrders } from "./LiveTodayOrders";
import { Section } from "../../components/Section";
import { Meter, StateLine } from "./liveParts";
import { GuideLink } from "../../components/GuideLink";
import { DryRunChip } from "./DryRunChip";

/**
 * The Live page's `today` tab (2026-10-15): every live loop's day on one page -- what is armed,
 * what is open, what settled, and anything that needs attention -- over `GET /api/live/today`
 * (`readers/liveToday.ts`, composed from the per-loop readers). Since 2026-11-06 it follows the
 * portfolio tab's layout, in three sections by question: what needs attention (two summary cards: the
 * live loops' net, open book and attention chip, whose list opens in place and leads to each loop's card;
 * and the broker positions' counts, the first three to act on and a way into Positions,
 * `WhatToDo.tsx`), how each loop is doing (a parent card per loop with its breaker as a meter, which opens a
 * lightbox with the loop's orders today as chains, `LiveTodayOrders.tsx`, and every field the old table's
 * detail view carried), and what was traded by hand (trade cards, only where there is something to say).
 * A module that is off and holds nothing has no card and no line. The net's method sits behind the
 * card's ?. There is no button here that touches an order. The one control is the arm-for-today strip
 * on a MEIC (main account) or ladder card (`ArmSwitch.tsx`), drawn only while the server says
 * `live_arming` is on (it ships off); it asks the module's own command to write or delete the day's
 * arm record, and never clears the halt flag.
 *
 * Money follows the one layout: "net" is net, costs sit in their own columns, settlement never
 * recorded apart reads n/r. The suite totals today's realised net and, beside it, what the loops hold
 * at mark: every loop states both net of fees. The worst case stays each loop's own measure and is
 * never added up.
 */
export { useLiveToday };

/** The header badge's words: how many loops are armed now, and the halt flag. */
export function suiteBadgeText(d: LiveTodayPayload): string {
  const n = d.suite.armed.length;
  const armed = n === 0 ? "none armed" : `${n} armed`;
  return d.halted ? `${armed} · HALTED` : armed;
}

function armWords(l: LiveTodayLoop): { text: string; cls: string } {
  const a = l.arm;
  if (a.armed && a.pastDisarm) return { text: "past disarm", cls: "muted" };
  if (a.armed) return { text: "ARMED", cls: "pnl-pos" };
  if (a.stale) return { text: "stale arm", cls: "muted" };
  return { text: "not armed", cls: "muted" };
}

function gateWords(v: boolean | null): string {
  return v === null ? "unknown" : v ? "on" : "off";
}

function settlementWords(v: number | null, recorded: number, trades: number): string {
  return v === null ? `n/r (${recorded} of ${trades})` : fmtMoney(v);
}

/** No limit configured is "no limit", never "ok": a breaker that cannot fire is not a clear one. */
function breakerWords(l: LiveTodayLoop): string {
  const b = l.breaker;
  if (b.limit === null) return "no limit";
  if (b.tripped === null) return `unknown · limit ${fmtMoney(b.limit)}`;
  // MEIC's breaker sums the ledger's gross pnl, not net: the figure says which, so it is never read
  // beside the net column as the same kind of number.
  const kind = b.figureKind === "gross" ? " gross" : "";
  return `${b.tripped ? "TRIPPED" : "ok"} · ${fmtMoney(b.figure)}${kind} of −${fmtMoney(Math.abs(b.limit))}`;
}

function loopStateWords(l: LiveTodayLoop): string {
  if (l.loopState === null) {
    if (l.loop === "meic") return "no meic-live job";
    if (l.loop.startsWith("meic.")) return `no meic-live.${l.loop.slice("meic.".length)} job`;
    return "no loop record";
  }
  if (l.loopState.state === "no-data") return "no iterations";
  const age = l.loopState.ageSeconds;
  return age === null ? l.loopState.state : `${l.loopState.state} · ${age < 120 ? `${Math.round(age)}s` : `${Math.round(age / 60)}m`} ago`;
}

/** The hand row's net, stated beside the suite total and never in it. */
export function handNote(d: LiveTodayPayload): string {
  const h = d.hand;
  if (h === undefined || h.status !== "ok" || h.orders.length === 0) return "No 0DTE trade by hand today.";
  const net = h.net === null ? "n/r" : `${fmtMoney(h.net)}${h.openLegs.length > 0 ? " at mark" : ""}`;
  return `By hand: ${net}, not in the loops' total.`;
}

/** One loop's open book at its own mark, or the hand trades' net with their held legs at mark. */
export interface OpenAtMark {
  key: string;
  label: string;
  value: number | null;
  title: string;
}

/**
 * What is open now, at each loop's own mark (2026-11-07): a loop holding something, or with a mark,
 * is listed with its figure and the mark's basis; the hand trades are listed while a leg is held.
 * These are the parts of the suite's unrealised total (`suite.unrealised`, every loop net of fees to
 * date); the hand trades are listed beside it, never in it. Empty when nothing is open anywhere.
 */
export function openAtMark(d: LiveTodayPayload): OpenAtMark[] {
  const out: OpenAtMark[] = d.loops
    .filter((l) => !l.retired && l.ledger === "ok" && (l.open > 0 || (l.mark?.value ?? null) !== null))
    .map((l) => ({
      key: l.loop,
      label: l.loop,
      value: l.mark?.value ?? null,
      title: `${l.loop}: ${l.open} open · ${l.mark?.basis ?? "no mark"}${l.mark?.at ? ` · as of ${etClock(l.mark.at, "?")} ET` : ""}`,
    }));
  const h = d.hand;
  if (h !== undefined && h.status === "ok" && h.openLegs.length > 0) {
    out.push({
      key: "hand",
      label: "by hand",
      value: h.net,
      title: `by hand: the day's net with ${h.openLegs.length} held leg${h.openLegs.length === 1 ? "" : "s"} at mark (realised and open together); never in the loops' total`,
    });
  }
  return out;
}

/** True when any loop has a working order: the simple view then shows the `working` column too. */
export function anyWorking(loops: readonly LiveTodayLoop[]): boolean {
  return loops.some((l) => !l.retired && l.ledger === "ok" && (l.working ?? 0) > 0);
}

/** What a loop's net today is made of, or why it is n/r: the net's hover wherever it is shown. */
function netTitleOf(l: LiveTodayLoop): string | undefined {
  const t = l.today;
  if (t === null) return undefined;
  if (t.net === null) return `a trade resolved today never recorded its fees, so its net is unknown; ${fmtMoney(t.netRecorded)} over the other ${t.feesTrades}`;
  return `gross ${fmtMoney(t.gross)} − fees ${t.fees === null ? "n/r" : fmtMoney(t.fees)} − settlement ${t.settlementFees === null ? "n/r" : fmtMoney(t.settlementFees)}`;
}

/**
 * The loops table's columns. The simple view keeps whether a loop is armed, its state, what is open,
 * what it can lose, the mark, the day's net and the breaker; the gate, the counts and the net's parts
 * are the detail view's. `working` shows in simple too whenever an order is working.
 */
function loopColumns(working: boolean): Column<LiveTodayLoop>[] {
  return [
    {
      key: "loop",
      label: "loop",
      td: (l) => (
        <td>
          <Link to={l.to}>{l.loop}</Link>
          {l.ledger !== "ok" && (
            <span className={l.ledger === "failed" ? "pnl-neg" : "muted"} title={l.ledgerError ?? undefined}>
              {" "}
              · {l.ledger === "absent" ? "no ledger" : "read failed"}
            </span>
          )}
        </td>
      ),
    },
    {
      key: "arm",
      label: "arm",
      td: (l) => {
        const arm = armWords(l);
        return (
          <td
            className={arm.cls}
            title={`arm record ${l.arm.date ?? "none"}${l.arm.at !== null ? ` (written ${l.arm.at})` : ""}; disarm ${l.arm.disarmTime} ET`}
          >
            {arm.text}
          </td>
        );
      },
    },
    {
      key: "gate",
      label: "gate · symbol",
      detailOnly: true,
      td: (l) => (
        <td title="the module's live master switch, and the symbol (and arm) the loop is pinned to">
          {gateWords(l.arm.liveEnabled)} · {l.arm.symbol ?? "?"}
          {l.arm.arm !== null ? ` · ${l.arm.arm}` : ""}
        </td>
      ),
    },
    { key: "state", label: "state", td: (l) => <td className="muted">{loopStateWords(l)}</td> },
    { key: "open", label: "open", td: (l) => <td>{l.ledger === "ok" ? fmtNum(l.open, 0) : "—"}</td> },
    {
      key: "working",
      label: "working",
      detailOnly: !working,
      td: (l) => <td title={l.workingBasis}>{l.ledger === "ok" ? fmtNum(l.working, 0) : "—"}</td>,
    },
    { key: "worst", label: "worst case", td: (l) => <td title={l.worstCaseBasis}>{fmtMoney(l.worstCase)}</td> },
    {
      key: "mark",
      label: "mark",
      td: (l) => (
        <td title={l.mark?.basis}>
          <PnlCell v={l.mark?.value ?? null} />
        </td>
      ),
    },
    { key: "entries", label: "entries", detailOnly: true, td: (l) => <td>{l.ledger === "ok" ? fmtNum(l.entriesToday, 0) : "—"}</td> },
    {
      key: "resolved",
      label: "resolved",
      detailOnly: true,
      td: (l) => <td title={l.today?.basis}>{l.today !== null ? fmtNum(l.today.trades, 0) : "—"}</td>,
    },
    { key: "gross", label: "gross", detailOnly: true, td: (l) => <td>{l.today !== null ? fmtMoney(l.today.gross) : "—"}</td> },
    {
      key: "fees",
      label: "fees",
      detailOnly: true,
      td: (l) => {
        const t = l.today;
        return (
          <td title={t !== null && t.fees === null ? `fees recorded on ${t.feesTrades} of ${t.trades} trades resolved today` : undefined}>
            {t === null ? "—" : t.fees === null ? `n/r (${t.feesTrades} of ${t.trades})` : fmtMoney(t.fees)}
          </td>
        );
      },
    },
    {
      key: "settlement",
      label: "settlement",
      detailOnly: true,
      td: (l) => <td>{l.today !== null ? settlementWords(l.today.settlementFees, l.today.settlementTrades, l.today.trades) : "—"}</td>,
    },
    {
      key: "net",
      label: "net",
      td: (l) => {
        const t = l.today;
        return (
          <td
            title={netTitleOf(l)}
          >
            {t !== null && t.net === null ? <span className="muted">n/r</span> : <PnlCell v={t?.net ?? null} />}
          </td>
        );
      },
    },
    {
      key: "breaker",
      label: "loss breaker",
      td: (l) => (
        <td className={l.breaker.tripped === true ? "pnl-neg" : "muted"} title={l.breaker.basis}>
          {breakerWords(l)}
        </td>
      ),
    },
  ];
}

/** The loop's fields as label / value rows, from the same cells the table used: the card shows the
 *  simple view's, the lightbox's detail tab every one. */
function LoopFields({ l, cols }: { l: LiveTodayLoop; cols: Column<LiveTodayLoop>[] }) {
  return (
    <table className="lt-fields">
      <tbody>
        {cols
          .filter((c) => c.key !== "loop")
          .map((c) => (
            <tr key={c.key}>
              <th scope="row">{c.label}</th>
              {c.td(l)}
            </tr>
          ))}
      </tbody>
    </table>
  );
}

/** An arm as the page names it: the registry arm, or "arm not recorded" where the ledger has none. */
export function armName(arm: string | null): string {
  return arm ?? "arm not recorded";
}

/** A pair's net today: n/r where any of its rows never recorded fees, never a partial sum. */
function pairNet(p: LiveTodayPair): { text: string; cls: string } {
  if (p.today.trades === 0) return { text: "—", cls: "muted" };
  return p.today.net === null ? { text: "n/r", cls: "muted" } : { text: fmtMoney(p.today.net), cls: tone(p.today.net) };
}

/**
 * Today by (arm, symbol), on the loop's card. Live results are read per arm and per symbol, never
 * pooled, so a day that ran two arms (or two symbols) shows two lines, each with its own net. Nothing
 * is drawn where the loop reports no pair: a section with no data is hidden, not an empty box.
 */
export function PairLines({ pairs }: { pairs: LiveTodayPair[] }) {
  if (pairs.length === 0) return null;
  return (
    <ul className="lt-pairs" aria-label="today by arm and symbol">
      {pairs.map((p) => {
        const net = pairNet(p);
        return (
          <li key={`${p.arm ?? ""}|${p.symbol}`} title={p.today.basis}>
            <span className="lt-pair-name">
              {armName(p.arm)} · {p.symbol}
            </span>
            <span className={`lt-pair-net ${net.cls}`}>{net.text}</span>
            <span className="muted lt-pair-count">
              {p.today.trades} resolved{p.open > 0 ? ` · ${p.open} open` : ""}
            </span>
          </li>
        );
      })}
    </ul>
  );
}

/** The detail tab's by-arm table: every pair in the money layout (gross − fees − settlement = net). */
function PairTable({ pairs }: { pairs: LiveTodayPair[] }) {
  if (pairs.length === 0) return null;
  return (
    <div className="table-scroll">
      <table className="data-table num-from-2">
        <caption className="muted" style={{ textAlign: "left", paddingBottom: 6 }}>
          today by arm and symbol
        </caption>
        <thead>
          <tr>
            <th>arm</th>
            <th>symbol</th>
            <th className="num">resolved</th>
            <th className="num">open</th>
            <th className="num lt-money">gross</th>
            <th className="num lt-money">fees</th>
            <th className="num lt-money">settlement</th>
            <th className="num">net</th>
          </tr>
        </thead>
        <tbody>
          {pairs.map((p) => {
            const t = p.today;
            return (
              <tr key={`${p.arm ?? ""}|${p.symbol}`}>
                <td>{armName(p.arm)}</td>
                <td>{p.symbol}</td>
                <td className="num">{t.trades}</td>
                <td className="num">{p.open}</td>
                <td className="num lt-money">{t.trades === 0 ? "—" : fmtMoney(t.gross)}</td>
                <td className="num lt-money">{t.trades === 0 ? "—" : t.fees === null ? `n/r (${t.feesTrades} of ${t.trades})` : fmtMoney(t.fees)}</td>
                <td className="num lt-money">{t.trades === 0 ? "—" : settlementWords(t.settlementFees, t.settlementTrades, t.trades)}</td>
                <td className={`num ${pairNet(p).cls}`}>{pairNet(p).text}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

/** Every field the loops table's detail view carried, plus what needs attention on this loop. */
export function LoopDetail({ l, attention }: { l: LiveTodayLoop; attention: LiveTodayPayload["attention"] }) {
  const mine = attention.filter((a) => a.loop === l.loop);
  return (
    <div className="lt-detail">
      <PairTable pairs={l.pairs} />
      <LoopFields l={l} cols={loopColumns(true)} />
      {mine.length > 0 ? (
        <ul className="lt-attention">
          {mine.map((a, i) => (
            <li key={i} className="pnl-neg">
              {kindWords(a.kind)}: {a.detail}
            </li>
          ))}
        </ul>
      ) : (
        <p className="muted">Nothing on this loop needs attention.</p>
      )}
      <p className="muted" style={{ margin: 0 }}>
        <Link to={l.to}>the loop's own page</Link> holds the rest.
      </p>
    </div>
  );
}

type LoopTab = "orders" | "detail";

function LoopDialog({
  loops,
  index,
  data,
  onStep,
  onClose,
}: {
  loops: LiveTodayLoop[];
  index: number;
  data: LiveTodayPayload;
  onStep: (to: number) => void;
  onClose: () => void;
}) {
  const [tab, setTab] = useState<LoopTab>("orders");
  const l = loops[index];
  if (l === undefined) return null;
  const arm = armWords(l);
  const net = l.today?.net ?? null;
  const tabs: LightboxTab<LoopTab>[] = [
    { id: "orders", label: "orders" },
    { id: "detail", label: "detail" },
  ];
  return (
    <CardLightbox count={loops.length} index={index} onStep={onStep} onClose={onClose}>
      {(nav) => (
        <LightboxFrame
          kicker={`live · ${data.session}`}
          title={l.loop}
          chips={
            <>
              <span className={`chip ${l.arm.armed && !l.arm.pastDisarm ? "chip-ok" : ""}`}>{arm.text}</span>
              <span className="chip">{loopStateWords(l)}</span>
              <span className={`chip ${tone(net)}`}>net today {l.today !== null && net === null ? "n/r" : fmtMoney(net)}</span>
            </>
          }
          tabs={tabs}
          tab={tab}
          onTab={setTab}
          position={nav.position}
          onPrev={nav.prev}
          onNext={nav.next}
          onClose={onClose}
        >
          {tab === "orders" ? <LoopOrders loop={l.loop} session={data.session} /> : <LoopDetail l={l} attention={data.attention} />}
        </LightboxFrame>
      )}
    </CardLightbox>
  );
}

const NET_HELP: CardHelpContent = {
  title: "How net today is made",
  body: (
    <>
      <HelpWarn title="Realised, not marked">
        <p>
          Gross less fees less settlement, over the positions resolved today, at the broker&apos;s fills. The worst case is each loop&apos;s
          own measure (hover for which) and is not added across loops. A trade by hand is stated beside the total and never in it.
        </p>
      </HelpWarn>
      <HelpSection title="Open at mark">
        <p>
          What the loops still hold, at mark, net of fees to date: flies at the loop&apos;s own mid mark, bwb at its usable mark, MEIC at
          the stream cache&apos;s mid; the ladder keeps no mark on a held lot, so it reads n/r while it holds one. The first figure totals the loops; each loop&apos;s part follows. A loop holding something it cannot
          mark (or whose fees are unrecorded) reads n/r, and so does the total, rather than a part that reads as the whole. By hand is the
          day&apos;s net with its held legs at mark, beside the total and never in it.
        </p>
      </HelpSection>
      <HelpSection title="Armed and halt">
        <p>A loop is armed when its arm record is dated today and the disarm time has not passed. The halt flag is suite-wide and blocks new entries on every loop; clearing it arms nothing. Where the console&apos;s arm switches are turned on (<code>live_arming</code>), a loop&apos;s card has one; arming is refused while the halt is up.</p>
      </HelpSection>
      <GuideLink to="/help/live#today" />
    </>
  ),
};

const LOOP_HELP: CardHelpContent = {
  title: "Reading a loop",
  body: (
    <>
      <HelpSection title="Worst case and mark">
        <p>
          The worst case is each loop&apos;s own measure, said in its hover: at expiry for flies, the stop distance for MEIC, the box for bwb, the whole cost of the open lots for the ladder (shares carry no stop).
          It is never added across loops. The mark is unrealised net of fees to date on every loop, and the top card totals it.
        </p>
      </HelpSection>
      <HelpSection title="Loss breaker">
        <p>The day&apos;s loss against the loop&apos;s own limit. A tripped breaker stops new entries. No limit configured reads as &quot;no limit&quot;, never as clear. MEIC&apos;s figure is gross, and says so.</p>
      </HelpSection>
      <HelpSection title="Orders and detail">
        <p>Opens the loop&apos;s orders today, one chain per position, and every field the card leaves out: the gate, the counts and the net&apos;s parts.</p>
      </HelpSection>
      <GuideLink to="/help/live#today" />
    </>
  ),
};

/** The breaker's share used: the day's loss over the limit, nothing while there is no limit or no figure. */
export function breakerShare(b: LiveTodayLoop["breaker"]): number | null {
  if (b.limit === null || b.figure === null || b.limit === 0) return null;
  return (Math.max(0, -b.figure) / Math.abs(b.limit)) * 100;
}

/** The loop card's element id, where the attention list's "Go to" scrolls. A loop id may hold a dot (`meic.<login>`). */
export const loopCardId = (loop: string) => `loop-card-${loop}`;

/** A loop's parent card: its net today, what is open and what it can lose, the breaker as a meter, and
 *  a button to its orders and detail. */
function LoopParentCard({
  l,
  showWorking,
  attention,
  found,
  onOpen,
  armSwitch,
}: {
  l: LiveTodayLoop;
  showWorking: boolean;
  /** What needs attention on this loop, from the same list the chip opens. */
  attention: number;
  /** The chip's list just led here: outline the card for a moment. */
  found: boolean;
  onOpen: () => void;
  /** The arm-for-today strip's inputs: present only while `live_arming` is on and the loop has a switch. */
  armSwitch?: { sw: LiveLoopSwitch; attempt: ArmAttempt | null; halted: boolean };
}) {
  const arm = armWords(l);
  const t = l.today;
  const net = t?.net ?? null;
  const losing = net !== null && net < 0;
  const flagged = l.breaker.tripped === true || l.ledger === "failed";
  const share = breakerShare(l.breaker);
  const tiles = [
    { label: "open", value: l.ledger === "ok" ? fmtNum(l.open, 0) : "—", title: undefined },
    ...(showWorking ? [{ label: "working", value: l.ledger === "ok" ? fmtNum(l.working, 0) : "—", title: l.workingBasis }] : []),
    { label: "worst case", value: fmtMoney(l.worstCase), title: l.worstCaseBasis },
    { label: "mark", value: l.mark === null ? "—" : fmtMoney(l.mark.value), title: l.mark?.basis },
  ];
  return (
    <Card
      id={loopCardId(l.loop)}
      title={<Link to={l.to}>{l.loop}</Link>}
      collapseKey={`${l.loop} loop`}
      className={["lv-4", "pf-cq", "lt-loop", losing ? "is-neg" : "", flagged ? "is-flagged" : "", found ? "is-found" : ""].filter(Boolean).join(" ")}
      help={LOOP_HELP}
      controls={
        <>
          {attention > 0 && <span className="chip chip-bad">{attention} need{attention === 1 ? "s" : ""} attention</span>}
          <span className={`chip ${l.arm.armed && !l.arm.pastDisarm ? "chip-ok" : ""}`}>{arm.text}</span>
          {l.dryRun != null && <DryRunChip state={l.dryRun} />}
        </>
      }
    >
      {armSwitch !== undefined ? <ArmStrip l={l} sw={armSwitch.sw} attempt={armSwitch.attempt} halted={armSwitch.halted} /> : null}
      <div className={`pf-hero ${t !== null && net === null ? "muted" : tone(net)}`} title={netTitleOf(l)}>
        {t !== null && net === null ? "n/r" : fmtMoney(net)}
        <span className="pf-unit"> net today</span>
      </div>
      {l.ledger !== "ok" ? (
        <StateLine tone={l.ledger === "failed" ? "bad" : "note"} title={l.ledgerError ?? undefined}>
          {l.ledger === "absent" ? "no ledger" : "read failed"}
        </StateLine>
      ) : null}
      <PairLines pairs={l.pairs} />
      <div className={`pf-tiles pf-tiles-${tiles.length > 3 ? 4 : 3}`}>
        {tiles.map((x) => (
          <Tile key={x.label} label={x.label} value={x.value} title={x.title} />
        ))}
      </div>
      <span className="lv-lab">loss breaker</span>
      {share !== null ? <Meter pct={share} title={l.breaker.basis} /> : null}
      <p className={`pf-caption ${l.breaker.tripped === true ? "pnl-neg" : "muted"}`} title={l.breaker.basis}>
        {breakerWords(l)}
      </p>
      <p className="muted pf-caption">{loopStateWords(l)}</p>
      <div className="pf-mores pf-push">
        <button type="button" className="pf-more" onClick={onOpen} aria-label={`${l.loop}: open its orders and detail`}>
          orders and detail <span aria-hidden="true">›</span>
        </button>
      </div>
    </Card>
  );
}

/** Why the unrealised total reads as it does, for its hover. */
export function unrealisedTitle(u: LiveTodayPayload["suite"]["unrealised"]): string {
  if (u.value === null) return `Not totalled: ${u.unmarked.map((m) => `${m.loop} — ${m.reason}`).join("; ")}`;
  if (u.summed.length === 0) return "Nothing is open in any loop.";
  return `Unrealised net of fees to date over ${u.summed.join(", ")}, at each loop's mark${u.at ? ` (oldest ${etClock(u.at, "?")} ET)` : ""}. The hand trades are not in it.`;
}

/** The open-at-mark line under the realised net: the loops' unrealised total, then each loop's part
 *  in its own tone with its basis on hover, then the hand trades beside it. */
function OpenLine({ items, total }: { items: OpenAtMark[]; total: LiveTodayPayload["suite"]["unrealised"] }) {
  return (
    <p className="lt-open" aria-label="open at mark">
      <span className="lt-open-lab">open at mark</span>
      {items.length === 0 ? (
        <span className="muted">nothing open</span>
      ) : (
        <>
          <span className="lt-open-total" title={unrealisedTitle(total)}>
            <span className={total.value === null ? "muted" : tone(total.value)}>{total.value === null ? "n/r" : fmtMoney(total.value)}</span>
          </span>
          {items.map((i) => (
            <span key={i.key} className="lt-open-item" title={i.title}>
              {i.label} <span className={i.value === null ? "muted" : tone(i.value)}>{i.value === null ? "n/r" : fmtMoney(i.value)}</span>
            </span>
          ))}
        </>
      )}
    </p>
  );
}

/** A loop's arm strip, or nothing: the switches are drawn only while `live_arming` is on, and only for a
 *  loop the server gave a switch (flies and bwb have none). */
export function armFor(l: LiveTodayLoop, d: LiveTodayPayload, arming: LiveArmingState | undefined): { sw: LiveLoopSwitch; attempt: ArmAttempt | null; halted: boolean } | undefined {
  if (arming === undefined || !arming.enabled || l.retired) return undefined;
  const sw = arming.switches.find((s) => s.loop === l.loop);
  if (sw === undefined) return undefined;
  return { sw, attempt: latestAttempt(arming.attempts, l.loop), halted: d.halted };
}

export function LiveTodayView({ data: d, manage, arming }: { data: LiveTodayPayload; manage?: ManagePayload; arming?: LiveArmingState }) {
  const s = d.suite;
  const lightbox = useOpenParam("loop");
  const active = d.loops.filter((l) => !l.retired);
  const allHands = [...(d.hand !== undefined ? [d.hand] : []), ...(d.handOthers ?? [])];
  const handsShown = allHands.filter(handHasContent);
  const index = active.findIndex((l) => l.loop === lightbox.open);
  const showWorking = anyWorking(d.loops);
  // The attention list's "Go to": scroll to the loop's card and outline it for a moment.
  const [found, setFound] = useState<string | null>(null);
  useEffect(() => {
    if (found === null) return;
    const t = setTimeout(() => setFound(null), 2500);
    return () => clearTimeout(t);
  }, [found]);
  const goToLoop = (loop: string) => {
    const still = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    document.getElementById(loopCardId(loop))?.scrollIntoView({ behavior: still ? "auto" : "smooth", block: "center" });
    setFound(loop);
  };
  const netTitle =
    `${handNote(d)} ` +
    (s.net === null
      ? s.feesUnrecorded.length > 0 && s.gross !== null
        ? `Not totalled: fees were never recorded on a trade resolved today in ${s.feesUnrecorded.join(", ")}, so its net is unknown.`
        : `Not totalled: ${s.excluded.map((e) => `${e.loop} — ${e.reason}`).join("; ")}`
      : `Realised net today over ${s.summed.join(", ") || "no loop"}: gross − fees − settlement, positions resolved today.` +
        (s.excluded.length > 0 ? ` Left out: ${s.excluded.map((e) => `${e.loop} (${e.reason})`).join(", ")}.` : ""));
  return (
    <div className="pf-layout">
      <Section concept="stand" question="What needs attention?">
        <Card
          title="net today · all loops"
          collapseKey="today-net"
          className="lv-4 lt-summary"
          help={NET_HELP}
          flag="realised, not marked"
        >
          <div className={`pf-hero ${s.net === null ? "muted" : tone(s.net)}`} title={netTitle}>
            {s.net === null ? "n/r" : fmtMoney(s.net)}
            <span className="pf-unit">
              {" "}
              {s.gross === null
                ? "n/r"
                : `gross ${fmtMoney(s.gross)} · fees ${s.fees === null ? "n/r" : fmtMoney(s.fees)} · settlement ${s.settlementFees === null ? "n/r" : fmtMoney(s.settlementFees)}`}{" "}
              · {s.trades === null ? "n/r" : String(s.trades)} resolved
            </span>
          </div>
          <OpenLine items={openAtMark(d)} total={s.unrealised} />
          <span className="lt-hero-chips">
            <span className={`chip ${s.armed.length > 0 ? "chip-ok" : ""}`} title="A loop is armed when its arm record is dated today and the disarm time has not passed.">
              armed · {d.session}: {s.armed.length === 0 ? `none of ${s.loops}` : `${s.armed.length} of ${s.loops} · ${s.armed.join(", ")}`}
            </span>
            <span className={`chip ${d.halted ? "chip-bad" : ""}`} title="state/halt-live.flag, suite-wide: blocks new entries on every loop.">
              halt flag {d.halted ? "HALTED" : "clear"}
            </span>
            <AttentionChip items={d.attention} hasCard={(loop) => active.some((l) => l.loop === loop)} onGo={goToLoop} />
          </span>
        </Card>
        <BrokerSummaryCard className="lv-8 lt-summary" manage={manage} />
      </Section>
      <Section concept="income" question="How is each loop doing?">
        {active.map((l) => (
          <LoopParentCard
            key={l.loop}
            l={l}
            showWorking={showWorking}
            attention={d.attention.filter((a) => a.loop === l.loop).length}
            found={found === l.loop}
            onOpen={() => lightbox.show(l.loop)}
            armSwitch={armFor(l, d, arming)}
          />
        ))}
      </Section>
      {/* A disabled module holding nothing has no card and no line; anything open, working, held, orphaned
          or overdue (or an arm record for today) brings its card back. The by-hand section shows only the
          accounts that have something to say: trades, a warning, or a view that could not be read. */}
      {handsShown.length > 0 && (
        <Section concept="add" question="What was traded by hand?">
          {handsShown.map((h, i) => (
            <HandCard
              key={h.account ?? `main-${String(i)}`}
              hand={h}
              session={d.session}
              note={handHasTrades(h) ? undefined : handNote(d)}
              showAccount={allHands.length > 1}
            />
          ))}
        </Section>
      )}
      {index >= 0 ? (
        <LoopDialog loops={active} index={index} data={d} onStep={(to) => lightbox.step(active[to]?.loop ?? "")} onClose={lightbox.close} />
      ) : null}
    </div>
  );
}

export function LiveTodayPage() {
  const { data, isLoading, isError } = useLiveToday();
  const manage = useManage(undefined);
  const arming = useLiveArming();
  if (isError) return <p className="pnl-neg">The live loops could not be read.</p>;
  if (isLoading || data === undefined) return <p className="muted">loading the live loops…</p>;
  return <LiveTodayView data={data} manage={manage.data} arming={arming.data} />;
}
