import type { LiveTodayHand, LiveTodayHandOrder } from "@console/shared";
import { Card } from "../../components/DataTable";
import { HelpSection, HelpWarn, type CardHelpContent } from "../../components/CardHelp";
import { ListViewContext, ViewSwitch, useViewChoice } from "../../components/ViewSwitch";
import { fmtCash, fmtMoney, fmtPrice } from "../../lib/format";
import { etClock } from "../../lib/etTime";
import { Tile } from "../Flies/PerformanceTab";
import { StateLine } from "./liveParts";
import { TradeCards, type TradeCardModel } from "./TradeCard";

/**
 * Live → today's hand row and card (2026-11-03): the 0DTE trades placed by hand, which no loop
 * manages, stops, force-closes or settles. Everything is packages/manage's `zero_dte` block as
 * `readers/liveHand.ts` shaped it: the split from the loops' fills, the reconciliation and the money
 * are manage's. Its net sits beside the suite total and is never added to it.
 */

/** `-1 7555P` from an OCC symbol and the fill's action. */
export function legLabel(symbol: string, action: string, quantity: number | null): string {
  const s = symbol.replace(/\s+/g, "");
  const m = /([CP])(\d{8})$/.exec(s);
  const q = quantity ?? 1;
  const signed = action.startsWith("Sell") ? -q : q;
  const sign = signed > 0 ? "+" : "−";
  if (m === null) return `${sign}${Math.abs(signed)} ${symbol}`;
  const strike = Number(m[2]) / 1000;
  return `${sign}${Math.abs(signed)} ${strike}${m[1]}`;
}

/** The order's net per-share price (`1.80 cr`) when every leg is the same size; null otherwise. */
export function orderPrice(o: LiveTodayHandOrder): number | null {
  const sizes = new Set(o.legs.map((l) => l.quantity));
  if (sizes.size !== 1) return null;
  const q = [...sizes][0];
  return q === null || q === undefined || q === 0 ? null : Math.round((o.cash / (100 * q)) * 100) / 100;
}

/** A mid as quoted: `0.025` stays three places (a nickel-wide 0DTE quote), whole cents stay two. */
export function fmtMid(v: number | null): string {
  if (v === null) return "—";
  return Math.round(v * 1000) % 10 === 0 ? v.toFixed(2) : v.toFixed(3);
}

/** `2 expired legs still listed by the broker … (2026-11-03: −1 7555P +1 7545P)`, one clause per
 *  expiry; null when nothing expired is listed. */
export function expiredWords(legs: LiveTodayHand["expiredHeld"]): string | null {
  if (legs.length === 0) return null;
  const byExpiry = new Map<string, string[]>();
  for (const l of legs) {
    const q = l.quantity ?? 0;
    const key = l.expiry ?? "?";
    byExpiry.set(key, [...(byExpiry.get(key) ?? []), legLabel(l.symbol, q < 0 ? "Sell" : "Buy", Math.abs(q))]);
  }
  const parts = [...byExpiry].sort(([a], [b]) => a.localeCompare(b)).map(([exp, labels]) => `${exp}: ${labels.join(" ")}`);
  const n = legs.length;
  return `${n} expired ${n === 1 ? "leg" : "legs"} still listed by the broker until it processes the expiry, not counted here (${parts.join("; ")})`;
}

/** The one-line state for the row's `state` cell. */
export function handStateWords(h: LiveTodayHand): string {
  if (h.status === "none") return h.reason ?? "no evaluation today";
  if (h.status === "unread") return `not read: ${h.reason ?? "unknown"}`;
  const snap = etClock(h.snapshotAt, "?");
  return h.markedAt !== null ? `fills ${snap} · marked ${etClock(h.markedAt, "?")}` : `as of ${snap}`;
}

/** True when the hand has something to show as trades: an order today, or a held leg. */
export function handHasTrades(h: LiveTodayHand): boolean {
  return h.status === "ok" && (h.orders.length > 0 || h.openLegs.length > 0);
}

/** `STO` / `BTC` ... from the fill's action ("Sell to Open"); null when the action names no side or no open/close. */
function legTag(action: string): string | null {
  const side = action.startsWith("Sell") ? "ST" : action.startsWith("Buy") ? "BT" : null;
  const end = /to Open/i.test(action) ? "O" : /to Close/i.test(action) ? "C" : null;
  return side !== null && end !== null ? `${side}${end}` : null;
}

/** `1 4980P @ 2.50` from an OCC symbol, the fill's size and its price. */
function legText(symbol: string, quantity: number | null, price: number | null): string {
  const m = /([CP])(\d{8})$/.exec(symbol.replace(/\s+/g, ""));
  const q = Math.abs(quantity ?? 1);
  const what = m === null ? symbol.trim() : `${Number(m[2]) / 1000}${m[1]}`;
  return `${q} ${what}${price === null ? "" : ` @ ${fmtMid(price)}`}`;
}

const KIND_ROLE: Record<LiveTodayHandOrder["kind"], string> = { open: "open", close: "close", mixed: "open and close", unknown: "order" };

/** One hand order as a trade card: what filled, at what price, what it took in and what it cost. A
 *  hand trade has no worst case here, so the card has no bar. */
export function handModel(o: LiveTodayHandOrder, underlying: string): TradeCardModel {
  const price = orderPrice(o);
  const priceWords = price === null ? "—" : fmtPrice(price);
  const clock = etClock(o.at);
  return {
    key: o.orderId ?? o.at ?? "",
    symbol: underlying,
    spot: null,
    shape: `${o.legs.length}-leg order · 0DTE · ${clock}`,
    chips: <span className="chip">{KIND_ROLE[o.kind]}</span>,
    cash: o.cash,
    side: [priceWords === "—" ? "mixed sizes" : priceWords, clock],
    bar: null,
    hideBar: true,
    barTitle: "",
    label: "no worst case is computed for hand trades",
    open: { date: clock, price: priceWords, role: KIND_ROLE[o.kind] },
    legs: o.legs.map((l) => {
      const tag = legTag(l.action);
      return { buy: !l.action.startsWith("Sell"), ...(tag === null ? {} : { tag }), text: legText(l.symbol, l.quantity, l.price), quote: null };
    }),
    after: [o.orderId === null ? "no order id" : `order ${o.orderId}`],
    foot: [`fees ${fmtMoney(o.fees)}`, "not in the loops' total"],
  };
}

export const HAND_HELP: CardHelpContent = {
  title: "Trades placed by hand",
  body: (
    <>
      <HelpWarn title="No loop manages these">
        <p>
          No stop, no force-close, no settlement row, and no breaker counts them. A fill is a loop&apos;s when its order id is one a loop
          has published; every other 0DTE fill on the names manage leaves to the loops is the hand&apos;s.
        </p>
      </HelpWarn>
      <HelpSection title="The money">
        <p>
          Net is gross less fees less settlement, the held legs at mid until they settle. It sits beside the loops&apos; total and is never added to it. Expired legs the broker
          still lists are not counted.
        </p>
      </HelpSection>
    </>
  ),
};

/** The hand trades: a parent card with the money, each order as a trade card (or a row), and the lines
 *  that put a figure in doubt. Drawn alone with a reason when there is nothing to show. */
export function HandCard({ hand: h, session, note }: { hand: LiveTodayHand; session: string; note?: string }) {
  const [view, setView] = useViewChoice("today-hand");
  const warnings = [
    ...h.unreconciled.map(
      (u) => `${u.symbol.trim()}: ${u.held} held, but today's fills explain ${u.loopFills + u.handFills} (loops ${u.loopFills}, hand ${u.handFills}); mark and net are not stated`,
    ),
    ...(h.afterPublish.length > 0 ? [`filled after the loops last listed their orders, so possibly a loop's: ${h.afterPublish.join(", ")}`] : []),
  ];
  const expired = expiredWords(h.expiredHeld);
  const ok = h.status === "ok";
  const title = h.underlyings.length > 0 ? `by hand · ${h.underlyings.join(", ")} 0DTE` : "by hand · 0DTE";
  const underlying = h.underlyings[0] ?? "?";
  return (
    <Card
      title={title}
      collapseKey="today-hand"
      className="lv-12 pf-cq"
      help={HAND_HELP}
      flag="no loop manages these"
      controls={handHasTrades(h) && h.orders.length > 0 ? <ViewSwitch view={view} onChange={setView} /> : undefined}
    >
      <p className="muted lv-line">
        {session} · {handStateWords(h)}
        {ok && h.unreconciled.length > 0 ? <span className="pnl-neg"> · does not reconcile</span> : null}
      </p>
      {note ? <StateLine>{note}</StateLine> : null}
      {handHasTrades(h) ? (
        <div className="pf-tiles pf-tiles-4">
          <Tile label="net" value={h.net === null ? "n/r" : fmtMoney(h.net)} tone={h.net === null ? "dim" : h.net >= 0 ? "pos" : "neg"} title={h.netBasis ?? undefined} />
          <Tile label="gross" value={h.gross === null ? "—" : fmtMoney(h.gross)} title="signed cash from today's hand fills, plus the held legs at mid" />
          <Tile label="fees" value={h.fees === null ? "—" : fmtMoney(h.fees)} />
          <Tile label="settlement" value={h.settlement === null ? "n/r" : fmtMoney(h.settlement)} title="n/r while a held leg has still to settle (booked the next morning)" />
        </div>
      ) : null}
      {h.openLegs.length > 0 ? (
        <StateLine>
          Held:{" "}
          {h.openLegs.map((l) => (
            <span key={l.symbol} title={`${l.markSource ?? "?"} mark${l.markAgeSeconds !== null ? `, ${Math.round(l.markAgeSeconds)}s old` : ""}`}>
              {legLabel(l.symbol, l.quantity < 0 ? "Sell" : "Buy", Math.abs(l.quantity))} @ {fmtMid(l.mid)}{" "}
            </span>
          ))}
          · mark {fmtCash(h.mark)}
        </StateLine>
      ) : null}
      <ListViewContext.Provider value={view}>
        <TradeCards models={h.orders.map((o) => handModel(o, underlying))} />
      </ListViewContext.Provider>
      {warnings.map((n) => (
        <StateLine key={n} tone="bad">
          {n}
        </StateLine>
      ))}
      {h.caveats.map((n) => (
        <StateLine key={n}>{n}</StateLine>
      ))}
      {expired !== null ? (
        <StateLine title="the broker lists an expired leg until it processes the expiry overnight">{expired}</StateLine>
      ) : null}
    </Card>
  );
}
