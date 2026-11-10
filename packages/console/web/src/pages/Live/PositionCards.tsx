import { useRef, useState, type ReactNode } from "react";
import { ViewSwitch, useViewChoice } from "../../components/ViewSwitch";
import { FilterSortButton, FilterSortPanel, FilterSummary, SortSection, sortRows, type SortChoice, type SortOption } from "../../components/FilterSort";
import { useSearchParams } from "react-router-dom";
import type { CandleBar, ManageChainOrder, ManageTrade } from "@console/shared";
import { useCandles } from "../../lib/useCandles";
import { fmtCash, fmtMoney } from "../../lib/format";
import { CardLightbox, LightboxFrame, useOpenParam, type LightboxTab } from "../Backtest/CardLightbox";
import { ACTION_LABEL, ActionChip, Kv, Money, tone } from "./manageParts";
import { useLiveMarks } from "./LiveMarks";
import { useQuote } from "../../lib/useQuote";
import { liveTrade } from "./liveLegs";
import { useTickFlash } from "../../lib/useTickFlash";
import type { QuoteState } from "../../lib/wsClient";
import { chipState, formatChange, formatPct, formatPrice } from "../../components/shell/FuturesTicker";

/**
 * Live → positions as cards (2026-11-04, the user's pick "A" off the mock-up canvas): one card per
 * trade with its broker order chain down the card, open → rolls → "now, close at mid", as the
 * broker's own client draws it. A card opens the shared lightbox: the whole chain with every leg,
 * and the evaluation's detail (verdict, levels, closing order, legs).
 *
 * Every figure is packages/manage's: the chain's orders, their signed cash, the close at mid and the
 * P&L. The running total under an order is the sum of the chain's own signed cash to that order,
 * nothing re-priced. A trade with no reconciled chain shows its opening alone and says so. A losing
 * trade is red throughout (its P&L, its bar and its card's edge); the order is the reader's choice,
 * P&L high to low or low to high, and a trade with no net sorts last either way.
 */

export type PnlOrder = "desc" | "asc";
type Filter = "all" | "act" | "rolled";
type Tab = "chain" | "evaluation";

const PASSIVE = new Set(["HOLD", "SETTLES"]);

/** P&L net, high to low or low to high; a trade whose net is unknown goes last in both. */
export function sortByPnl(trades: ManageTrade[], order: PnlOrder): ManageTrade[] {
  return [...trades].sort((a, b) => {
    if (a.pnlNet === null && b.pnlNet === null) return a.tradeId.localeCompare(b.tradeId);
    if (a.pnlNet === null) return 1;
    if (b.pnlNet === null) return -1;
    return order === "desc" ? b.pnlNet - a.pnlNet : a.pnlNet - b.pnlNet;
  });
}

export interface ChainStep {
  date: string | null;
  role: string;
  price: string;
  cash: number | null;
  fees: number | null;
  /** The chain's signed cash summed to this order; null once any earlier order's cash is unknown. */
  total: number | null;
  legs: ChainLeg[];
}

/** One leg of an order: its broker action (BTO, STO, BTC, STC) split off the rest of the line. */
export interface ChainLeg {
  action: string | null;
  buy: boolean | null;
  text: string;
}

/** An order's legs, one per line: the side that set the net first (a debit's buys, a credit's sells). */
export function chainLegs(legs: string[], side: "cr" | "db" | null): ChainLeg[] {
  const parsed = legs.map((l): ChainLeg => {
    const [, action, text] = /^(BTO|STO|BTC|STC)\s+(.*)$/.exec(l) ?? [];
    return action && text !== undefined ? { action, buy: action.startsWith("B"), text } : { action: null, buy: null, text: l };
  });
  if (side === null) return parsed;
  const first = side === "db";
  // A stable sort keeps the broker's order within each side.
  return [...parsed].sort((a, b) => Number(b.buy === first) - Number(a.buy === first));
}

function priceText(price: number | null, side: "cr" | "db" | null): string {
  return price === null || side === null ? "—" : `${price.toFixed(2)} ${side}`;
}

/** The steps a card draws: the broker's chain when one was reconciled, else the opening alone. */
export function chainSteps(t: ManageTrade): ChainStep[] {
  const orders: ManageChainOrder[] = t.chain?.orders ?? [];
  if (orders.length > 0) {
    let total: number | null = 0;
    return orders.map((o, i) => {
      total = total === null || o.net === null ? null : total + o.net;
      return {
        date: o.date,
        role: i === 0 ? "open" : o.role,
        price: priceText(o.netPrice, o.netSide),
        cash: o.net,
        fees: o.fees,
        total,
        legs: chainLegs(o.legs, o.netSide),
      };
    });
  }
  if (t.opening) {
    return [{ date: null, role: "open", price: priceText(t.opening.netPrice, t.opening.netSide), cash: t.opening.net, fees: null, total: t.opening.net, legs: [] }];
  }
  return [];
}

function pct(v: number | null): string {
  return v === null ? "—" : `${v >= 0 ? "+" : ""}${(v * 100).toFixed(0)}%`;
}

/** What capture means on this trade: a credit's share kept, a debit's return. It is manage's gross
 *  figure (the one its take-profit judges), so beside the card's net P&L it says so. */
export function captureText(t: ManageTrade): string | null {
  if (t.capture === null) return null;
  const share = t.kind === "debit" ? `${pct(t.capture)} on the debit` : `${pct(t.capture)} of the credit kept`;
  return `${share}, before fees`;
}

function dteText(t: ManageTrade): string | null {
  if (t.nearestShortDte === null) return null;
  return `${t.nearestShortDte} DTE${t.nearestShortExpiry ? ` · ${t.nearestShortExpiry}` : ""}`;
}

function rolls(t: ManageTrade): number {
  return t.chain?.rolls ?? 0;
}

function Timeline({ t, full }: { t: ManageTrade; full: boolean }) {
  const steps = chainSteps(t);
  return (
    <ol className="pos-chain" aria-label="order chain">
      {steps.map((s, i) => (
        <li key={i} className={`pos-step pos-step-${s.role}`}>
          <span className="pos-dot" aria-hidden="true" />
          <div className="pos-step-body">
            <div className="pos-step-head">
              <span className="pos-step-date">{s.date ?? "opened"}</span>
              <span className="pos-role">{s.role}</span>
              <span className="pos-step-price">{s.price}</span>
              {full ? <span className={`pos-step-cash ${tone(s.cash)}`}>{fmtCash(s.cash)}</span> : null}
            </div>
            {s.legs.length > 0 ? (
              <ul className="pos-legs">
                {s.legs.map((l, j) => (
                  <li key={j} className="pos-leg">
                    {l.action ? <span className={`pos-leg-act ${l.buy ? "is-buy" : "is-sell"}`}>{l.action}</span> : null}
                    <span>{l.text}</span>
                  </li>
                ))}
              </ul>
            ) : null}
            {full ? (
              <span className="pos-step-foot">
                fees {s.fees === null ? "—" : fmtMoney(Math.abs(s.fees))} · running total <span className={tone(s.total)}>{fmtCash(s.total)}</span>
              </span>
            ) : null}
          </div>
        </li>
      ))}
      <li className="pos-step pos-step-now">
        <span className="pos-dot" aria-hidden="true" />
        <div className="pos-step-body">
          <div className="pos-step-now-line">
            <span>
              now · close at mid <span className={`pos-num ${tone(t.value)}`}>{fmtCash(t.value)}</span>
            </span>
            {t.chain ? (
              <span>
                {t.chain.cash !== null && t.chain.cash < 0 ? "paid in" : "taken in"}{" "}
                <span className={`pos-num ${tone(t.chain.cash)}`}>{fmtCash(t.chain.cash)}</span>
              </span>
            ) : null}
          </div>
        </div>
      </li>
      {t.basis === "open_legs" ? <li className="pos-chain-note">no reconciled chain: from the open legs, so earlier rolls are not counted</li> : null}
    </ol>
  );
}

function PnlFigure({ t }: { t: ManageTrade }) {
  return (
    <span
      className={`pos-card-pnl ${tone(t.pnlNet)}`}
      title={t.pnlNet === null ? `fees not read, so no net; gross ${fmtMoney(t.pnlGross)}` : `net of fees · gross ${fmtMoney(t.pnlGross)}`}
    >
      {fmtMoney(t.pnlNet)}
    </span>
  );
}

/** The last completed session's daily candle: its close, and its change on the session before. */
export interface SessionClose {
  date: string;
  close: number;
  change: number | null;
  changePct: number | null;
}

function etClock(now: Date): { date: string; minutes: number } {
  const parts = Object.fromEntries(
    new Intl.DateTimeFormat("en-CA", {
      timeZone: "America/New_York",
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      hourCycle: "h23",
    })
      .formatToParts(now)
      .map((p) => [p.type, p.value]),
  );
  return { date: `${parts.year}-${parts.month}-${parts.day}`, minutes: Number(parts.hour) * 60 + Number(parts.minute) };
}

/**
 * The newest completed session among daily candles (each starts at midnight UTC of its trading date).
 * Before 09:30 ET a bar dated today is the pre-market forming, never a session, so it is skipped; from
 * the close on, today's bar is the session just ended (a half day included).
 */
export function sessionClose(bars: CandleBar[], now: Date): SessionClose | null {
  const et = etClock(now);
  const done = bars
    .map((b) => ({ date: new Date(b.t * 1000).toISOString().slice(0, 10), c: b.c, t: b.t }))
    .filter((b) => b.date < et.date || (b.date === et.date && et.minutes >= 9 * 60 + 30))
    .sort((a, b) => a.t - b.t);
  const last = done[done.length - 1];
  if (last === undefined) return null;
  const prev = done[done.length - 2];
  const change = prev === undefined ? null : last.c - prev.c;
  return {
    date: last.date,
    close: last.c,
    change,
    changePct: change !== null && prev !== undefined && prev.c !== 0 ? (change / prev.c) * 100 : null,
  };
}

/**
 * The underlying's price beside its symbol. In the session: the live feed's price with its change and
 * change % on the day, against the feed's prior close, by the top bar's own rule (`chipState`). Out of
 * it: the last session's close from its daily candle, with its change on the session before. Without
 * either it is the evaluation's spot, muted, with no change: never a change between numbers from two
 * sources.
 */
export function SpotLine({
  q,
  evaluated,
  closed = null,
}: {
  q: QuoteState | undefined;
  evaluated: number | null;
  /** Out of the session: the last session's close; null in the session or before the bars arrive. */
  closed?: SessionClose | null;
}) {
  const { price, change, changePct } = chipState(q);
  const live = q?.source === "dxlink" && price !== undefined;
  if (closed !== null) {
    return (
      <span className="pos-card-spot" title={`the ${closed.date} session's close, from its daily candle`}>
        {formatPrice("", closed.close)}
        {closed.change !== null && closed.changePct !== null ? (
          <span className={closed.change > 0 ? "pnl-pos" : closed.change < 0 ? "pnl-neg" : "muted"}>
            {" "}
            {formatChange("", closed.change)} {formatPct(closed.changePct)}
          </span>
        ) : null}
        <span className="muted"> close</span>
      </span>
    );
  }
  if (!live) {
    return evaluated === null ? null : (
      <span className="pos-card-spot muted" title="the evaluation's spot; no live price">
        {formatPrice("", evaluated)}
      </span>
    );
  }
  return (
    <span className="pos-card-spot" title={q?.prevClose !== undefined ? `prior close ${formatPrice("", q.prevClose)}` : undefined}>
      {formatPrice("", price)}
      {change !== undefined && changePct !== undefined ? (
        <span className={change > 0 ? "pnl-pos" : change < 0 ? "pnl-neg" : "muted"}>
          {" "}
          {formatChange("", change)} {formatPct(changePct)}
        </span>
      ) : null}
    </span>
  );
}

/** The card's spot, live on the quote socket and flashing on each tick as the top bar's chips do; out
 *  of the session (the watchdog's word), the last session's daily close. */
function CardSpot({ t, inSession }: { t: ManageTrade; inSession: boolean }) {
  const q = useQuote(t.underlying);
  const bars = useCandles(inSession ? "" : t.underlying, "1d");
  const closed = inSession ? null : sessionClose(bars, new Date());
  const ref = useRef<HTMLSpanElement>(null);
  useTickFlash(ref, q, inSession);
  return (
    <span ref={ref} className="pos-card-spot-wrap">
      <SpotLine q={q} evaluated={t.spot} closed={closed} />
    </span>
  );
}

export function PositionCard({ t: evaluated, onOpen, inSession = true }: { t: ManageTrade; onOpen: () => void; inSession?: boolean }) {
  // The headline, the capture bar and "close at mid" on the live marks when every leg has one; the
  // evaluation's net stays in the small print. The verdict and its levels are the evaluation's.
  const { t, live, evaluatedNet } = liveTrade(evaluated, useLiveMarks()?.trades.get(evaluated.tradeId));
  const losing = t.pnlNet !== null && t.pnlNet < 0;
  const bar = t.capture === null ? 0 : Math.min(100, Math.abs(t.capture) * 100);
  const n = rolls(t);
  const fees = t.fees === null ? null : Math.abs(t.fees);
  return (
    <button type="button" className={losing ? "pos-card is-neg" : "pos-card"} onClick={onOpen} aria-label={`${t.tradeId}: ${ACTION_LABEL[t.action] ?? t.action}, P&L ${fmtMoney(t.pnlNet)}`}>
      <span className="pos-card-head">
        <span className="pos-card-titles">
          <span className="pos-card-sym-line">
            <span className="pos-card-sym">{t.tradeId}</span>
            <CardSpot t={t} inSession={inSession} />
          </span>
          <span className="pos-card-shape">{[t.shape, t.intent].filter(Boolean).join(" · ") || "—"}</span>
        </span>
        <span className="pos-card-chips">
          {t.earningsTrade ? <span className={`chip ${t.earningsTrade.passed ? "chip-bad" : "chip-warn"}`}>earnings</span> : null}
          <ActionChip action={t.action} />
        </span>
      </span>
      <span className="pos-card-figs">
        <PnlFigure t={t} />
        <span className="pos-card-side">
          {captureText(t) ?? "—"}
          <br />
          {dteText(t) ?? ""}
        </span>
      </span>
      <span className="pos-bar" aria-hidden="true">
        <span className={t.capture !== null && t.capture < 0 ? "pos-bar-fill is-neg" : "pos-bar-fill"} style={{ width: `${bar}%` }} />
      </span>
      <span className="pos-chain-label">
        order chain · {n === 0 ? "no rolls" : `${n} roll${n === 1 ? "" : "s"}`}
        {live ? (
          <span title="the P&L above is on the live marks; this is the evaluation's, at its snapshot">
            {" "}
            · live · evaluated <span className={tone(evaluatedNet)}>{fmtMoney(evaluatedNet)}</span>
          </span>
        ) : null}
      </span>
      <Timeline t={t} full={false} />
      <span className="pos-card-foot">
        <span>fees {fmtMoney(fees)}</span>
        <span className="pos-card-reason">{t.reason}</span>
      </span>
    </button>
  );
}

function ChainTab({ t: evaluated }: { t: ManageTrade }) {
  // The same live marks as the card: the rows below still add up (close at mid less the cost is the
  // gross, less the fees the net), all live or all the evaluation's.
  const { t, live, evaluatedNet } = liveTrade(evaluated, useLiveMarks()?.trades.get(evaluated.tradeId));
  const fees = t.fees === null ? null : Math.abs(t.fees);
  return (
    <div className="pos-open">
      <Timeline t={t} full />
      <aside className="pos-open-side">
        <div className="lbl">P&amp;L, whole chain{live ? " · live" : ""}</div>
        <PnlFigure t={t} />
        <Kv
          rows={[
            [t.chain && t.chain.cash !== null && t.chain.cash < 0 ? "net paid in" : "net taken in", <Money key="c" v={t.chain?.cash ?? t.opening?.net ?? null} />],
            [live ? "close at mid, live" : "close at mid", <Money key="m" v={t.value} />],
            ["P&L gross", <Money key="g" v={t.pnlGross} />],
            ["fees paid", fmtMoney(fees)],
            ["P&L net of fees", <Money key="n" v={t.pnlNet} />],
            ...(live ? [["P&L net, as evaluated", <Money key="e" v={evaluatedNet} />] as [string, ReactNode]] : []),
          ]}
        />
      </aside>
    </div>
  );
}

/** What a trade's ID is: provisional (no order chain yet, so it will change once the orders are known),
 * or the survivor of a join that folded other IDs into it. Nothing for a settled ID. */
export function idChips(t: ManageTrade): ReactNode {
  return (
    <>
      {t.tradeIdProvisional ? (
        <span className="chip muted" title="no order chain yet: this ID changes once the trade's orders are known">
          provisional ID
        </span>
      ) : null}
      {t.mergedIds.length ? (
        <span className="chip muted" title="a join made one trade of these; the older ID is kept">
          merged {t.mergedIds.join(", ")}
        </span>
      ) : null}
    </>
  );
}

function TradeDialog({
  trades,
  index,
  detail,
  onStep,
  onClose,
}: {
  trades: ManageTrade[];
  index: number;
  detail: (t: ManageTrade) => ReactNode;
  onStep: (to: number) => void;
  onClose: () => void;
}) {
  const [tab, setTab] = useState<Tab>("chain");
  const t = trades[index];
  if (t === undefined) return null;
  const tabs: LightboxTab<Tab>[] = [
    { id: "chain", label: "order chain", badge: rolls(t) > 0 ? `${rolls(t)} roll${rolls(t) === 1 ? "" : "s"}` : null },
    { id: "evaluation", label: "evaluation" },
  ];
  return (
    <CardLightbox count={trades.length} index={index} onStep={onStep} onClose={onClose}>
      {(nav) => (
        <LightboxFrame
          kicker={[t.shape, t.kind, t.confidence !== null ? `confidence ${t.confidence}` : null].filter(Boolean).join(" · ")}
          title={t.tradeId}
          chips={
            <>
              <ActionChip action={t.action} />
              <span className={`chip ${tone(t.pnlNet)}`}>{fmtMoney(t.pnlNet)}</span>
              {idChips(t)}
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
          {tab === "chain" ? <ChainTab t={t} /> : detail(t)}
        </LightboxFrame>
      )}
    </CardLightbox>
  );
}

/** One line a trade (2026-11-06): ID, shape, net P&L, capture as its bar, days to the nearest short,
 *  manage's action. A row opens the trade's card in place; the card opens the lightbox as it always has. */
function PositionRows({ trades, inSession, onOpen }: { trades: ManageTrade[]; inSession: boolean; onOpen: (id: string) => void }) {
  const [open, setOpen] = useState<Set<string>>(new Set());
  const toggle = (k: string) => {
    const next = new Set(open);
    if (next.has(k)) next.delete(k);
    else next.add(k);
    setOpen(next);
  };
  return (
    <div className="tr-list pos-rows" role="list">
      {trades.map((t) => {
        const bar = t.capture === null ? 0 : Math.min(100, Math.abs(t.capture) * 100);
        return (
          <div key={t.tradeId} role="listitem" className="tr-item">
            <button type="button" className="tr-row" aria-expanded={open.has(t.tradeId)} onClick={() => toggle(t.tradeId)}>
              <span className="tr-caret" aria-hidden="true">
                ▸
              </span>
              <span className="tr-sym">{t.tradeId}</span>
              <span className="tr-shape">{[t.shape, t.intent].filter(Boolean).join(" · ") || "—"}</span>
              <span className={`tr-cash ${tone(t.pnlNet)}`}>{fmtMoney(t.pnlNet)}</span>
              <span className="tr-cap" title="capture, before fees">
                <span className="pos-bar">
                  <span className={t.capture !== null && t.capture < 0 ? "pos-bar-fill is-neg" : "pos-bar-fill"} style={{ width: `${bar}%` }} />
                </span>
                <span className="tr-cap-label">{captureText(t) ?? "—"}</span>
              </span>
              <span className="tr-after">{dteText(t) ?? "—"}</span>
              <span className="tr-chips">
                <ActionChip action={t.action} />
              </span>
            </button>
            {open.has(t.tradeId) ? (
              <div className="tr-open">
                <PositionCard t={t} inSession={inSession} onOpen={() => onOpen(t.tradeId)} />
              </div>
            ) : null}
          </div>
        );
      })}
    </div>
  );
}

/** The positions board's sorts (2026-11-06): P&L by default, then timing, capture, action, risk. */
export const POSITION_SORTS: SortOption<ManageTrade>[] = [
  { id: "pnl", label: "P&L", hint: "high to low", key: (t) => (t.pnlNet === null ? null : -t.pnlNet) },
  { id: "dte", label: "days to the nearest short", hint: "soonest first", key: (t) => t.nearestShortDte },
  { id: "capture", label: "capture", hint: "most kept first", key: (t) => (t.capture === null ? null : -t.capture) },
  {
    id: "action",
    label: "needs action first",
    hint: "action first, then P&L",
    key: (t) => (PASSIVE.has(t.action) ? 1e12 : 0) - (t.pnlNet ?? -1e11),
  },
  { id: "downside", label: "downside", hint: "largest first", key: (t) => t.profile?.downsideFromNow ?? null },
];

/** The board: the show chips and the filter & sort button, then a card per trade. `detail` renders the evaluation tab. */
export function PositionCards({
  trades,
  loading,
  detail,
  inSession = true,
}: {
  trades: ManageTrade[];
  loading: boolean;
  detail: (t: ManageTrade) => ReactNode;
  /** The watchdog's word on the session; out of it each card shows the last session's close. */
  inSession?: boolean;
}) {
  const [params, setParams] = useSearchParams();
  const lightbox = useOpenParam("trade");
  // The sort is in the URL (`?sort=<id>&rev=1`), so a link keeps it; an older link's `pnl=asc` is P&L reversed.
  const legacyAsc = params.get("pnl") === "asc";
  const sortId = POSITION_SORTS.some((s) => s.id === params.get("sort")) ? params.get("sort")! : "pnl";
  const choice: SortChoice = { id: sortId, reversed: params.get("rev") === "1" || (sortId === "pnl" && legacyAsc && !params.get("sort")) };
  const option = POSITION_SORTS.find((s) => s.id === choice.id);
  const [panel, setPanel] = useState(false);
  const anchor = useRef<HTMLButtonElement>(null);
  const [view, setView] = useViewChoice("positions");
  const raw = params.get("show");
  const filter: Filter = raw === "act" || raw === "rolled" ? raw : "all";
  const setParam = (k: string, v: string | null) => {
    const next = new URLSearchParams(params);
    if (v === null) next.delete(k);
    else next.set(k, v);
    setParams(next, { replace: true });
  };
  const acting = trades.filter((t) => !PASSIVE.has(t.action));
  const rolled = trades.filter((t) => rolls(t) > 0);
  const shown = sortRows(filter === "act" ? acting : filter === "rolled" ? rolled : trades, option, choice.reversed);
  // A trade named in the URL but filtered out still opens, stepping through every trade.
  const stepTrades = shown.some((t) => t.tradeId === lightbox.open) ? shown : sortRows(trades, option, choice.reversed);
  const changed = choice.id !== "pnl" || choice.reversed ? 1 : 0;
  const setSort = (c: SortChoice) => {
    const next = new URLSearchParams(params);
    next.delete("pnl");
    if (c.id === "pnl") next.delete("sort");
    else next.set("sort", c.id);
    if (c.reversed) next.set("rev", "1");
    else next.delete("rev");
    setParams(next, { replace: true });
  };
  const reset = () => setSort({ id: "pnl", reversed: false });
  const index = stepTrades.findIndex((t) => t.tradeId === lightbox.open);
  const chip = (on: boolean) => (on ? "chip chip-ok" : "chip");
  if (loading && trades.length === 0) return <p className="muted">Loading the evaluation…</p>;
  if (trades.length === 0) return <p className="muted">No trades in this account.</p>;
  return (
    <section className="pos-board">
      <div className="pos-controls">
        <div className="history-controls" role="group" aria-label="Show">
          <button type="button" className={chip(filter === "all")} aria-pressed={filter === "all"} onClick={() => setParam("show", null)}>
            all {trades.length}
          </button>
          <button type="button" className={chip(filter === "act")} data-hue="warn" aria-pressed={filter === "act"} onClick={() => setParam("show", "act")}>
            needs action {acting.length}
          </button>
          <button type="button" className={chip(filter === "rolled")} data-hue="special" aria-pressed={filter === "rolled"} onClick={() => setParam("show", "rolled")}>
            rolled {rolled.length}
          </button>
        </div>
        <span className="pos-tools">
          <ViewSwitch view={view} onChange={setView} />
          <FilterSortButton changed={changed} open={panel} onOpen={() => setPanel(!panel)} anchor={anchor} />
        </span>
      </div>
      <FilterSummary
        parts={[`by ${option?.label ?? "P&L"}${choice.reversed ? ", reversed" : `, ${option?.hint ?? ""}`}`]}
        changed={changed}
        onOpen={() => setPanel(true)}
        onReset={reset}
      />
      <FilterSortPanel open={panel} onClose={() => setPanel(false)} onReset={reset} anchor={anchor}>
        <SortSection sorts={POSITION_SORTS} choice={choice} fallback="pnl" onChange={setSort} />
      </FilterSortPanel>
      {shown.length === 0 ? (
        <p className="muted">{filter === "act" ? "Nothing to do today." : "No rolled trades."}</p>
      ) : view === "rows" ? (
        <PositionRows trades={shown} inSession={inSession} onOpen={(id) => lightbox.show(id)} />
      ) : (
        <div className="pos-cards">
          {shown.map((t) => (
            <PositionCard key={t.tradeId} t={t} inSession={inSession} onOpen={() => lightbox.show(t.tradeId)} />
          ))}
        </div>
      )}
      {index >= 0 ? (
        <TradeDialog trades={stepTrades} index={index} detail={detail} onStep={(to) => lightbox.step(stepTrades[to]?.tradeId ?? "")} onClose={lightbox.close} />
      ) : null}
    </section>
  );
}
