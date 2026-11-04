import { useState, type MouseEvent } from "react";
import { Link, useNavigate } from "react-router-dom";
import type { LiveTodayHand, LiveTodayLoop, LiveTodayPayload, ManagePayload } from "@console/shared";
import { PnlCell, fmtNum } from "../../components/DataTable";
import { type Column } from "../../components/columns";
import { fmtMoney } from "../../lib/format";
import { useLiveToday } from "../../lib/useLiveToday";
import { CardLightbox, LightboxFrame, useOpenParam, type LightboxTab } from "../Backtest/CardLightbox";
import { useManage } from "./ManagePage";
import { tone } from "./manageParts";
import { WhatToDoList, todoList } from "./WhatToDo";
import { HandCard, handCell } from "./HandToday";
import { LoopOrders } from "./LiveTodayOrders";

/**
 * The Live page's `today` tab (2026-10-15): every live loop's day on one page -- what is armed,
 * what is open, what settled, and anything that needs a person -- over `GET /api/live/today`
 * (`readers/liveToday.ts`, composed from the per-loop readers). Since 2026-11-04 it is a mosaic: a
 * large card (the suite's net, what to do today; it opens Live → positions) and a card per live loop,
 * which opens a lightbox with the loop's orders today as chains (`LiveTodayOrders.tsx`) and every
 * field the old table's detail view carried. There is no button here that touches an order.
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

/** The hand-placed 0DTE trades (2026-11-03) as a card beside the loops, from the same cells the
 *  table's hand row used; never in the loops' total. Its orders are the card below the board. */
const HAND_FIELDS = new Set(["loop", "state", "open", "mark", "net"]);

function HandTile({ hand, cols }: { hand: LiveTodayHand; cols: Column<LiveTodayLoop>[] }) {
  return (
    <div className="study-tile lt-loop lt-hand">
      <table className="lt-fields">
        <tbody>
          {cols
            .filter((c) => HAND_FIELDS.has(c.key))
            .map((c) => (
              <tr key={c.key}>
                <th scope="row">{c.key === "loop" ? "trades" : c.label}</th>
                {handCell(c.key, hand)}
              </tr>
            ))}
        </tbody>
      </table>
    </div>
  );
}

/** A loop's card: its name, armed and state, today's net, and the simple view's fields. */
function LoopCard({ l, cols, onOpen }: { l: LiveTodayLoop; cols: Column<LiveTodayLoop>[]; onOpen: () => void }) {
  const arm = armWords(l);
  const t = l.today;
  const net = t?.net ?? null;
  const losing = net !== null && net < 0;
  const flagged = l.breaker.tripped === true || l.ledger === "failed";
  return (
    <div
      role="button"
      tabIndex={0}
      className={["study-tile", "lt-loop", losing ? "is-neg" : "", flagged ? "is-flagged" : ""].filter(Boolean).join(" ")}
      onClick={onOpen}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          onOpen();
        }
      }}
      aria-label={`${l.loop}: open its orders and detail`}
    >
      <span className="lt-loop-top">
        <span className="lt-loop-name">
          <Link to={l.to} onClick={(e) => e.stopPropagation()}>
            {l.loop}
          </Link>
          {l.ledger !== "ok" && (
            <span className={l.ledger === "failed" ? "pnl-neg" : "muted"} title={l.ledgerError ?? undefined}>
              {" "}
              · {l.ledger === "absent" ? "no ledger" : "read failed"}
            </span>
          )}
        </span>
        <span className={`chip ${l.arm.armed && !l.arm.pastDisarm ? "chip-ok" : ""}`}>{arm.text}</span>
      </span>
      <span className="lt-loop-net" title={netTitleOf(l)}>
        {t !== null && net === null ? <span className="muted">n/r</span> : <span className={tone(net)}>{fmtMoney(net)}</span>}
        <span className="muted lt-loop-net-label">net today</span>
      </span>
      <LoopFields l={l} cols={cols.filter((c) => c.key !== "arm" && c.key !== "net")} />
    </div>
  );
}

export function LiveTodayView({ data: d, manage }: { data: LiveTodayPayload; manage?: ManagePayload }) {
  const s = d.suite;
  // A card keeps the simple view's fields in either view: everything else is its lightbox's detail.
  const cols = loopColumns(anyWorking(d.loops)).filter((c) => c.detailOnly !== true);
  const navigate = useNavigate();
  const lightbox = useOpenParam("loop");
  const active = d.loops.filter((l) => !l.retired);
  const retired = d.loops.filter((l) => l.retired);
  const index = active.findIndex((l) => l.loop === lightbox.open);
  // The big card goes to Live → positions; a link or button inside it keeps its own job.
  const toPositions = (e: MouseEvent<HTMLElement>) => {
    if ((e.target as HTMLElement).closest("a, button") !== null) return;
    navigate("/live/positions");
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
    <div className="lt-board">
      <div className="lt-tiles">
        <section className="study-tile study-hero lt-hero" onClick={toPositions}>
          <div className="lt-hero-top">
            <span className="lt-label">net today · all loops</span>
            <Link to="/live/positions" className="lt-hero-link">
              Live positions →
            </Link>
          </div>
          <span className={`lt-hero-net ${s.net === null ? "muted" : tone(s.net)}`} title={netTitle}>
            {s.net === null ? "n/r" : fmtMoney(s.net)}
          </span>
          <span className="lt-hero-parts">
            {s.gross === null
              ? "n/r"
              : `gross ${fmtMoney(s.gross)} · fees ${s.fees === null ? "n/r" : fmtMoney(s.fees)} · settlement ${s.settlementFees === null ? "n/r" : fmtMoney(s.settlementFees)}`}{" "}
            · {s.trades === null ? "n/r" : String(s.trades)} resolved
          </span>
          <span className="lt-hero-chips">
            <span className={`chip ${s.armed.length > 0 ? "chip-ok" : ""}`} title="A loop is armed when its arm record is dated today and the disarm time has not passed.">
              armed · {d.session}: {s.armed.length === 0 ? `none of ${s.loops}` : `${s.armed.length} of ${s.loops} · ${s.armed.join(", ")}`}
            </span>
            <span className={`chip ${d.halted ? "chip-bad" : ""}`} title="state/halt-live.flag, suite-wide: blocks new entries on every loop.">
              halt flag {d.halted ? "HALTED" : "clear"}
            </span>
            <span className={`chip ${d.attention.length > 0 ? "chip-bad" : ""}`}>needs a person · {d.attention.length}</span>
          </span>
          {/* "needs a person" became the first part of what to do today (2026-10-30). */}
          <WhatToDoList items={todoList(d, manage)} evaluated={manage?.session ?? null} />
        </section>
        {active.map((l) => (
          <LoopCard key={l.loop} l={l} cols={cols} onOpen={() => lightbox.show(l.loop)} />
        ))}
        {d.hand !== undefined && <HandTile hand={d.hand} cols={cols} />}
      </div>
      {retired.map((l) => (
        // A disabled module holding nothing folds to one line; anything open, working, held,
        // orphaned or overdue (or an arm record for today) brings its card back.
        <p key={l.loop} className="muted lt-retired" title="modules.<name>.enabled is false in the orchestrator config">
          <Link to={l.to}>{l.loop}</Link> off — module disabled, nothing open or working
        </p>
      ))}
      {d.hand !== undefined && <HandCard hand={d.hand} session={d.session} />}
      <p className="muted" style={{ fontSize: 11, margin: 0 }}>
        Net is realised today: gross − fees − settlement over positions resolved today, at the broker's fills. Worst case and mark are
        each loop's own measure (hover for which) and are not added across loops. {handNote(d)}
      </p>
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
