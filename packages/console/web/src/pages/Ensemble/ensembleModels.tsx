import type { EnsembleEntryRow, EnsembleHistoryRow, EnsembleLeg, EnsembleLine, EnsemblePosition } from "@console/shared";
import { fmtCash } from "../../lib/format";
import type { TradeCardLeg, TradeCardModel } from "../Live/TradeCard";

/**
 * ensemble's display transforms. Nothing here derives a result: the net, the costs and the drawdown are
 * the ledger's columns and the reader's sums. These only place them on cards and axes. The arms are
 * whatever the ledger names, so nothing below knows one.
 */

const money = (v: number | null): string =>
  v === null ? "—" : `$${Math.abs(v).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;

const HOW: Record<string, string> = {
  touch: "closed on a spot touch",
  profit_target: "closed at its profit target",
  time: "closed on the time exit",
  settlement: "held to the posted close",
};

export function howEnded(reason: string | null): string {
  return reason === null ? "closed" : (HOW[reason] ?? reason);
}

/** Every leg as one line of the order that opened the structure: sold legs STO, bought BTO. */
export function chainLegs(legs: readonly EnsembleLeg[], quantity: number, expiration: string): TradeCardLeg[] {
  return legs.map((l) => ({
    buy: l.side > 0,
    text: `${quantity} ${expiration} ${l.strike}${l.right}`,
    quote: l.role === "" ? null : l.role.replace(/_/g, " "),
  }));
}

/** The shape in a few words, from the legs alone: how many are sold, how many bought, and on which rights. */
export function shapeWords(legs: readonly EnsembleLeg[]): string {
  if (legs.length === 0) return "legs not recorded";
  const sold = legs.filter((l) => l.side < 0).length;
  const rights = [...new Set(legs.map((l) => l.right))].sort().join("/");
  return `${String(legs.length)} legs · ${String(sold)} sold, ${String(legs.length - sold)} bought · ${rights}`;
}

/** The blocking gate in a line: the reason, then the check and the value it read. */
export function gateWords(r: EnsembleEntryRow): string {
  const g = r.gate;
  if (g === null) return r.refusal ?? "—";
  const read = g.value === null ? "" : ` read ${g.check ?? "value"} = ${g.value}`;
  return `${g.reason}${read}`;
}

export interface TodayCounts {
  entered: number;
  pending: number;
  missed: number;
}

export function todayCounts(rows: readonly EnsembleEntryRow[]): TodayCounts {
  const n = (o: string) => rows.filter((r) => r.outcome === o).length;
  return { entered: n("entered"), pending: n("pending"), missed: n("missed") };
}

/** A line's curve with one point a session (the running net after the last trade that ended that day):
 *  a chart's time axis takes one value a day. */
export function dailyCurve(line: EnsembleLine): Array<{ x: string; y: number }> {
  const last = new Map<string, number>();
  for (const p of line.curve) last.set(p.x, p.y);
  return [...last.entries()].map(([x, y]) => ({ x, y }));
}

/** An open structure as a trade card: the order that opened it, then where it stands now. */
export function openModel(p: EnsemblePosition): TradeCardModel {
  const spot = p.mark?.spot ?? null;
  const mid = p.entryMid;
  const price = mid === null ? "—" : `${Math.abs(mid).toFixed(2)} ${mid >= 0 ? "cr" : "db"}`;
  return {
    key: p.positionId,
    symbol: p.symbol,
    spot: spot === null ? null : spot.toFixed(2),
    shape: `${p.arm} · ${shapeWords(p.legs)} · exp ${p.expiration}`,
    chips: <span className="chip">open</span>,
    // An open structure's headline is what it is worth now before costs: the cash in plus the mark.
    cash: p.unrealised,
    side: [`entered at ${price} at mid${p.quantity > 1 ? ` × ${String(p.quantity)}` : ""}`, `entered ${p.entrySession}`],
    bar: null,
    hideBar: true,
    barTitle: "",
    label: p.mark?.value == null ? "no usable mark yet" : `marked ${p.mark.ts === "" ? "" : p.mark.ts.slice(11, 16)}`.trim(),
    open: { date: p.entrySession, price },
    legs: chainLegs(p.legs, p.quantity, p.expiration),
    after: [
      `now · close at mid ${p.mark?.value == null ? "—" : fmtCash(p.mark.value)}`,
      `entry ${fmtCash(p.entryValue)}`,
      p.capital === null ? "max loss not recorded" : `max loss ${money(p.capital)}`,
    ],
    foot: [`unrealised ${fmtCash(p.unrealised)}`, "entry + mark, before any cost"],
  };
}

/** A closed structure as a trade card: the order that opened it, then how it ended and the costs apart. */
export function closedModel(r: EnsembleHistoryRow): TradeCardModel {
  const price = r.price === null ? "—" : `${Math.abs(r.price).toFixed(2)} ${r.price >= 0 ? "cr" : "db"}`;
  const q = r.quantity ?? 1;
  return {
    key: r.positionId,
    symbol: r.symbol,
    spot: null,
    shape: `${r.arm} · ${shapeWords(r.legs)} · exp ${r.expiration}`,
    chips: <span className={`chip ${(r.netPnl ?? 0) >= 0 ? "chip-ok" : "chip-warn"}`}>{r.exitReason ?? "closed"}</span>,
    cash: r.netPnl,
    side: [`net at the suite's slippage model`, `closed ${r.exitSession ?? "—"}`],
    bar: null,
    hideBar: true,
    barTitle: "",
    label: howEnded(r.exitReason),
    open: { date: r.entrySession, price },
    legs: chainLegs(r.legs, q, r.expiration),
    after: [
      `${howEnded(r.exitReason)} · exit ${fmtCash(r.exitCash)}`,
      `gross ${fmtCash(r.grossPnl)}`,
      `fees ${money(r.fees)} · settlement ${money(r.settlementFees)} · slippage ${money(r.slippage)}`,
    ],
    foot: [`net ${fmtCash(r.netPnl)}`, "gross − fees − settlement − slippage"],
  };
}
