import type { LadderHistoryRow, LadderOpenLot } from "@console/shared";
import type { TradeCardModel } from "../Live/TradeCard";
import { etClock } from "../../lib/etTime";
import { fmtCash, fmtMoney } from "../../lib/format";

/**
 * Display transforms for the ladder page, and nothing else: every figure is the module's (the ledger's
 * columns, `ladder status`, `ladder summary`). The page computes no level, no target and no P&L.
 */

/** `2026-11-19` as `11-19`: the year is on the page's date range, not on every row. */
export function monthDay(session: string | null): string {
  return session === null || session.length < 10 ? "—" : session.slice(5, 10);
}

/** A session and a time as the suite writes them, ET: `11-19 16:01`. */
export function whenEt(session: string | null, ts: string | null): string {
  if (session === null) return "—";
  return ts === null ? monthDay(session) : `${monthDay(session)} ${etClock(ts)}`;
}

export function price(v: number | null): string {
  return v === null ? "—" : v.toFixed(2);
}

/** The module stores net % as a percentage already (4.0093 is +4.01%): signed, to two places. */
export function pctOfCost(v: number | null): string {
  return v === null ? "—" : `${v >= 0 ? "+" : ""}${v.toFixed(2)}%`;
}

/**
 * How far the latest bid sits from an arm's next buy level, as a share of the level. A buy fills when the
 * ask reaches the level, so a bid above it is a fall still to come; at or under it the level has been
 * reached. No bid or no level reads as a dash.
 */
export function bidFromLevel(bid: number | null, level: number | null): { text: string; tone: "pos" | "dim" } | null {
  if (bid === null || level === null || level <= 0) return null;
  const pct = ((bid - level) / level) * 100;
  if (pct <= 0) return { text: pct === 0 ? "at the level" : `${Math.abs(pct).toFixed(1)}% under it`, tone: "pos" };
  return { text: `${pct.toFixed(1)}% above`, tone: "dim" };
}

const STAGE: Record<string, string> = { gain: "+4%", breakeven: "breakeven", floor: "95% of cost" };

/** The target and, for the decay arm, the step it has reached. */
export function targetText(lot: Pick<LadderOpenLot, "targetPrice" | "targetStage">): string {
  if (lot.targetPrice === null) return "—";
  const stage = lot.targetStage === null ? null : (STAGE[lot.targetStage] ?? lot.targetStage);
  return stage === null ? price(lot.targetPrice) : `${price(lot.targetPrice)} · ${stage}`;
}

const EXIT_WORDS: Record<string, string> = {
  target_gain: "sold at the +4% target",
  target_breakeven: "sold at breakeven after fees",
  target_floor: "sold at 95% of cost",
};

export function exitWords(reason: string | null): string {
  return reason === null ? "sold" : (EXIT_WORDS[reason] ?? reason);
}

const days = (n: number | null): string => (n === null ? "—" : `${String(n)} ${n === 1 ? "day" : "days"}`);

/**
 * One sold lot as a trade card in the console's order-chain look: the purchase as a BUY leg at the price
 * it filled, and a last step saying how it ended (the sale, the exit cash, the three costs apart).
 * Whole-position dollars, signed.
 */
export function lotModel(r: LadderHistoryRow): TradeCardModel {
  const q = r.quantity ?? 0;
  const gapped = r.buyPrice < r.buyLevel;
  return {
    key: r.positionId,
    symbol: r.symbol,
    spot: null,
    shape: `${String(q)} shares · ${r.arm} arm`,
    chips: <span className="chip chip-ok">{r.exitReason === "target_gain" ? "target" : (r.exitReason ?? "sold").replace("target_", "")}</span>,
    cash: r.netPnl,
    side: [`net ${pctOfCost(r.netPct)} of cost`, `sold ${monthDay(r.sellSession)}`],
    bar: null,
    hideBar: true,
    barTitle: "",
    label: `held ${days(r.holdDays)} · ${exitWords(r.exitReason)}`,
    open: { date: r.buySession, price: price(r.buyPrice), role: "buy" },
    legs: [
      {
        buy: true,
        tag: "BUY",
        text: `${String(q)} ${r.symbol} @ ${price(r.buyPrice)}`,
        quote: [
          r.refClose === null ? "" : `previous close ${price(r.refClose)}`,
          gapped ? `level ${price(r.buyLevel)}, filled lower on a gap` : `level ${price(r.buyLevel)}`,
        ]
          .filter(Boolean)
          .join(" · "),
      },
    ],
    after: [
      `sold ${whenEt(r.sellSession, r.sellTime)} ET at ${price(r.sellPrice)} · exit ${fmtCash(r.exitCash)}`,
      `gross ${fmtCash(r.grossPnl)}`,
      `fees ${fmtMoney(r.fees)} · settlement ${fmtMoney(r.settlementFees)} · slippage ${fmtMoney(r.slippage)}`,
    ],
    foot: [`net ${fmtCash(r.netPnl)}`, "gross − fees − settlement − slippage"],
  };
}
