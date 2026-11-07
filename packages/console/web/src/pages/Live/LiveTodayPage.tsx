import { useState } from "react";
import { Link } from "react-router-dom";
import type { LiveTodayLoop, LiveTodayPayload, ManagePayload } from "@console/shared";
import { Card, PnlCell, fmtNum } from "../../components/DataTable";
import { HelpSection, HelpWarn, type CardHelpContent } from "../../components/CardHelp";
import { type Column } from "../../components/columns";
import { fmtMoney } from "../../lib/format";
import { useLiveToday } from "../../lib/useLiveToday";
import { Tile } from "../Flies/PerformanceTab";
import { CardLightbox, LightboxFrame, useOpenParam, type LightboxTab } from "../Backtest/CardLightbox";
import { useManage } from "./ManagePage";
import { tone } from "./manageParts";
import { WhatToDoList, todoList } from "./WhatToDo";
import { HandCard, handHasTrades } from "./HandToday";
import { LoopOrders } from "./LiveTodayOrders";
import { Section } from "./PortfolioLayout";
import { Meter, StateLine } from "./liveParts";

/**
 * The Live page's `today` tab (2026-10-15): every live loop's day on one page -- what is armed,
 * what is open, what settled, and anything that needs a person -- over `GET /api/live/today`
 * (`readers/liveToday.ts`, composed from the per-loop readers). Since 2026-11-06 it follows the
 * portfolio tab's layout, in three sections by question: what needs a person (the suite's net, what to
 * do today), how each loop is doing (a parent card per loop with its breaker as a meter, which opens a
 * lightbox with the loop's orders today as chains, `LiveTodayOrders.tsx`, and every field the old table's
 * detail view carried), and what was traded by hand (trade cards). The net's method sits behind the
 * card's ?. There is no button here that touches an order.
 *
 * Money follows the one layout: "net" is net, costs sit in their own columns, settlement never
 * recorded apart reads n/r. The suite total sums only today's realised net, which every loop states
 * the same way; the worst case and the mark are each loop's own measure and are never added up.
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
  if (l.loopState === null) return l.loop === "meic" ? "no meic-live job" : "no loop record";
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

/** Every field the loops table's detail view carried, plus what needs a person on this loop. */
export function LoopDetail({ l, attention }: { l: LiveTodayLoop; attention: LiveTodayPayload["attention"] }) {
  const mine = attention.filter((a) => a.loop === l.loop);
  return (
    <div className="lt-detail">
      <LoopFields l={l} cols={loopColumns(true)} />
      {mine.length > 0 ? (
        <ul className="lt-attention">
          {mine.map((a, i) => (
            <li key={i} className="pnl-neg">
              {a.kind.replace(/_/g, " ")}: {a.detail}
            </li>
          ))}
        </ul>
      ) : (
        <p className="muted">Nothing on this loop needs a person.</p>
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
          Gross less fees less settlement, over the positions resolved today, at the broker&apos;s fills. Worst case and mark are each
          loop&apos;s own measure (hover for which) and are not added across loops. A trade by hand is stated beside the total and never in it.
        </p>
      </HelpWarn>
      <HelpSection title="Armed and halt">
        <p>A loop is armed when its arm record is dated today and the disarm time has not passed. The halt flag is suite-wide and blocks new entries on every loop; clearing it arms nothing.</p>
      </HelpSection>
    </>
  ),
};

const LOOP_HELP: CardHelpContent = {
  title: "Reading a loop",
  body: (
    <>
      <HelpSection title="Worst case and mark">
        <p>Each loop&apos;s own measure, said in the figure&apos;s hover: the worst case at expiry for flies, the stop distance for MEIC, the box for bwb. They are never added across loops.</p>
      </HelpSection>
      <HelpSection title="Loss breaker">
        <p>The day&apos;s loss against the loop&apos;s own limit. A tripped breaker stops new entries. No limit configured reads as &quot;no limit&quot;, never as clear. MEIC&apos;s figure is gross, and says so.</p>
      </HelpSection>
      <HelpSection title="Orders and detail">
        <p>Opens the loop&apos;s orders today, one chain per position, and every field the card leaves out: the gate, the counts and the net&apos;s parts.</p>
      </HelpSection>
    </>
  ),
};

/** The breaker's share used: the day's loss over the limit, nothing while there is no limit or no figure. */
export function breakerShare(b: LiveTodayLoop["breaker"]): number | null {
  if (b.limit === null || b.figure === null || b.limit === 0) return null;
  return (Math.max(0, -b.figure) / Math.abs(b.limit)) * 100;
}

/** A loop's parent card: its net today, what is open and what it can lose, the breaker as a meter, and
 *  a button to its orders and detail. */
function LoopParentCard({ l, showWorking, onOpen }: { l: LiveTodayLoop; showWorking: boolean; onOpen: () => void }) {
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
      title={<Link to={l.to}>{l.loop}</Link>}
      collapseKey={`${l.loop} loop`}
      className={["lv-4", "pf-cq", "lt-loop", losing ? "is-neg" : "", flagged ? "is-flagged" : ""].filter(Boolean).join(" ")}
      help={LOOP_HELP}
      controls={<span className={`chip ${l.arm.armed && !l.arm.pastDisarm ? "chip-ok" : ""}`}>{arm.text}</span>}
    >
      <div className={`pf-hero ${t !== null && net === null ? "muted" : tone(net)}`} title={netTitleOf(l)}>
        {t !== null && net === null ? "n/r" : fmtMoney(net)}
        <span className="pf-unit"> net today</span>
      </div>
      {l.ledger !== "ok" ? (
        <StateLine tone={l.ledger === "failed" ? "bad" : "note"} title={l.ledgerError ?? undefined}>
          {l.ledger === "absent" ? "no ledger" : "read failed"}
        </StateLine>
      ) : null}
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

export function LiveTodayView({ data: d, manage }: { data: LiveTodayPayload; manage?: ManagePayload }) {
  const s = d.suite;
  const lightbox = useOpenParam("loop");
  const active = d.loops.filter((l) => !l.retired);
  const retired = d.loops.filter((l) => l.retired);
  const index = active.findIndex((l) => l.loop === lightbox.open);
  const showWorking = anyWorking(d.loops);
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
      <Section concept="stand" question="What needs a person?">
        <Card
          title="net today · all loops"
          collapseKey="today-net"
          className="lv-8"
          help={NET_HELP}
          flag="realised, not marked"
          controls={
            <Link to="/live/positions" className="lt-hero-link">
              Live positions →
            </Link>
          }
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
          <span className="lt-hero-chips">
            <span className={`chip ${s.armed.length > 0 ? "chip-ok" : ""}`} title="A loop is armed when its arm record is dated today and the disarm time has not passed.">
              armed · {d.session}: {s.armed.length === 0 ? `none of ${s.loops}` : `${s.armed.length} of ${s.loops} · ${s.armed.join(", ")}`}
            </span>
            <span className={`chip ${d.halted ? "chip-bad" : ""}`} title="state/halt-live.flag, suite-wide: blocks new entries on every loop.">
              halt flag {d.halted ? "HALTED" : "clear"}
            </span>
            <span className={`chip ${d.attention.length > 0 ? "chip-bad" : ""}`}>needs a person · {d.attention.length}</span>
          </span>
        </Card>
        {/* "needs a person" is the first part of what to do today (2026-10-30). */}
        <WhatToDoList className="lv-4" items={todoList(d, manage)} evaluated={manage?.session ?? null} />
      </Section>
      <Section concept="income" question="How is each loop doing?">
        {active.map((l) => (
          <LoopParentCard key={l.loop} l={l} showWorking={showWorking} onOpen={() => lightbox.show(l.loop)} />
        ))}
      </Section>
      {retired.map((l) => (
        // A disabled module holding nothing folds to one line; anything open, working, held,
        // orphaned or overdue (or an arm record for today) brings its card back.
        <p key={l.loop} className="muted lt-retired" title="modules.<name>.enabled is false in the orchestrator config">
          <Link to={l.to}>{l.loop}</Link> off — module disabled, nothing open or working
        </p>
      ))}
      {d.hand !== undefined && (
        <Section concept="add" question="What was traded by hand?">
          <HandCard hand={d.hand} session={d.session} note={handHasTrades(d.hand) ? undefined : handNote(d)} />
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
  if (isError) return <p className="pnl-neg">The live loops could not be read.</p>;
  if (isLoading || data === undefined) return <p className="muted">loading the live loops…</p>;
  return <LiveTodayView data={data} manage={manage.data} />;
}
