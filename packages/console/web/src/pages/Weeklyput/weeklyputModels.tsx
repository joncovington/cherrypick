import type {
  WeeklyputCycle,
  WeeklyputEntryRow,
  WeeklyputPosition,
  WeeklyputStatus,
} from "@console/shared";
import { fmtCash } from "../../lib/format";
import type { TradeCardModel } from "../Live/TradeCard";

/**
 * weeklyput's display transforms. Nothing here derives a result: the cycle mean, the net and the
 * costs are the module's own columns and CLI answers. These only place them on cards and axes.
 */

/** item 57's bar: fewer finished cycles than this is a forward read, not a verdict. */
export const ITEM_57_CYCLES = 80;

export function fmtBp(v: number | null | undefined, digits = 1): string {
  if (v === null || v === undefined) return "—";
  return `${v >= 0 ? "+" : "-"}${Math.abs(v).toFixed(digits)} bp`;
}

const money = (v: number | null): string =>
  v === null ? "—" : `$${Math.abs(v).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;

/**
 * The cycle a name's row belongs to today: the one whose entry session is today; else the cycle held
 * now (entered on or before today, not yet expired); else the next one. Null when the ledger and the
 * calendar name none.
 */
export function pickCycle(status: WeeklyputStatus | undefined, entries: readonly WeeklyputEntryRow[]): string | null {
  if (status?.entryToday) return status.entryToday;
  const today = status?.date ?? null;
  const held = entries
    .filter((e) => today !== null && e.expiration >= today && e.entrySession <= today)
    .sort((a, b) => b.entrySession.localeCompare(a.entrySession));
  if (held[0] !== undefined) return held[0].expiration;
  if (status?.nextCycle) return status.nextCycle;
  return entries[0]?.expiration ?? null;
}

export interface NameRow {
  symbol: string;
  /** pending, entered, missed, or "none" when the register has no row yet. */
  outcome: string;
  refusal: string | null;
  position: WeeklyputPosition | null;
}

/** One row per declared name for a cycle, in the declared order, the register's row beside it. */
export function cycleRows(
  cycle: string | null,
  names: readonly string[],
  entries: readonly WeeklyputEntryRow[],
  positions: readonly WeeklyputPosition[],
): NameRow[] {
  const mine = entries.filter((e) => e.expiration === cycle);
  const symbols = [...new Set([...names, ...mine.map((e) => e.symbol)])];
  return symbols.map((symbol) => {
    const e = mine.find((x) => x.symbol === symbol);
    const position = e?.positionId ? (positions.find((p) => p.positionId === e.positionId) ?? null) : null;
    return { symbol, outcome: e?.outcome ?? "none", refusal: e?.refusal ?? null, position };
  });
}

/** How far spot sits from the strike, as a share of spot: positive is out of the money. */
export function distanceToStrike(p: WeeklyputPosition): number | null {
  const spot = p.mark?.spot ?? null;
  return spot === null || spot <= 0 ? null : (spot - p.strike) / spot;
}

export function distanceWords(p: WeeklyputPosition): string {
  const d = distanceToStrike(p);
  if (d === null) return "no spot recorded yet";
  const pct = `${(Math.abs(d) * 100).toFixed(1)}%`;
  return d >= 0 ? `spot ${pct} above the strike` : `spot ${pct} through the strike (in the money)`;
}

/** The cumulative per-cycle net in bp, oldest first: each finished cycle adds its own equal-weight mean. */
export function cumulativeBp(cycles: readonly WeeklyputCycle[]): Array<{ x: string; y: number }> {
  let total = 0;
  return cycles
    .filter((c) => c.finished && c.cycleNetBp !== null)
    .sort((a, b) => a.expiration.localeCompare(b.expiration))
    .map((c) => {
      total += c.cycleNetBp as number;
      return { x: c.expiration, y: Math.round(total * 10000) / 10000 };
    });
}

const HOW: Record<string, string> = {
  expired: "expired worthless",
  settled: "settled in the money in cash",
  assigned: "assigned: the shares delivered and sold at the same close",
};

/**
 * One put as a trade card in the console's order-chain look: the sale as an STO leg at its mid, and a
 * last step saying where it stands (open: the mark and the distance to the strike) or how it ended
 * (the posted close, the exit cash, and the three costs apart). Whole-position dollars, signed.
 */
export function putModel(p: WeeklyputPosition): TradeCardModel {
  const open = p.status === "open";
  const q = p.quantity;
  const mid = p.entryMid;
  const price = mid === null ? "—" : `${mid.toFixed(2)} cr`;
  const spot = p.mark?.spot ?? null;
  const quote = [
    p.entryBid !== null || p.entryAsk !== null ? `${p.entryBid ?? "—"} / ${p.entryAsk ?? "—"}` : "",
    p.entryDelta === null ? "" : `Δ ${p.entryDelta.toFixed(2)}`,
  ]
    .filter(Boolean)
    .join(" · ");
  const exposed = p.assignmentExposedAt !== null;
  const how = p.exitReason === null ? null : (HOW[p.exitReason] ?? p.exitReason);
  const chips = open ? (
    <>
      <span className="chip">open</span>
      {exposed ? (
        <span className="chip chip-warn" title="in the money with little time value left: early assignment is measured here, never modelled">
          assignment-exposed
        </span>
      ) : null}
    </>
  ) : (
    <>
      <span className={`chip ${p.exitReason === "expired" ? "chip-ok" : "chip-warn"}`}>{p.exitReason ?? "closed"}</span>
      {exposed ? <span className="chip chip-warn">was assignment-exposed</span> : null}
    </>
  );
  return {
    key: p.positionId,
    symbol: p.symbol,
    spot: spot === null ? null : spot.toFixed(2),
    shape: `short put · ${p.entryDelta === null ? "10" : (p.entryDelta * 100).toFixed(0)}Δ · exp ${p.expiration} · ${p.settlementStyle === "cash" ? "cash-settled" : "delivered"}`,
    chips,
    // An open put's headline is the credit it took in; a settled one's is its net.
    cash: open ? p.entryValue : p.netPnl,
    side: open
      ? [`${price} at mid${q > 1 ? ` × ${q}` : ""}`, `entered ${p.entrySession}`]
      : [`net ${p.netBp === null ? "—" : fmtBp(p.netBp)}`, `settled ${p.settleSession ?? "—"}`],
    bar: null,
    hideBar: true,
    barTitle: "",
    label: open ? distanceWords(p) : `posted close ${p.settlePrice === null ? "—" : p.settlePrice.toFixed(2)} against a ${p.strike} strike`,
    open: { date: p.entrySession, price },
    legs: [{ buy: false, tag: "STO", text: `${q} ${p.expiration} ${p.strike}P${mid === null ? "" : ` @ ${mid.toFixed(2)}`}`, quote: quote || null }],
    after: open
      ? [
          `now · close at mid ${p.mark?.value == null ? "—" : fmtCash(p.mark.value)}`,
          p.mark?.mid == null ? "no usable mark yet" : `mark ${p.mark.mid.toFixed(2)}`,
          "held to the posted close: no stop, no hedge",
        ]
      : [
          `${how ?? "closed"} · exit ${fmtCash(p.exitValue)}`,
          `gross ${fmtCash(p.grossPnl)}`,
          `fees ${money(p.fees)} · settlement ${money(p.settlement)} · slippage ${money(p.slippage)}`,
        ],
    foot: open
      ? [`entry credit ${fmtCash(p.entryValue)}`, `notional ${money(p.notional)}`]
      : [`net ${fmtCash(p.netPnl)}`, `gross ${fmtCash(p.grossPnl)} − fees − settlement − slippage`],
  };
}
