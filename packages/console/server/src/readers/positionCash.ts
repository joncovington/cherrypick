import type { TradeMoney, TradeTotals } from "@console/shared";
import { hasColumn, num, str, type DatabaseHandle } from "./db.js";

/**
 * The trade table standard (root CLAUDE.md) for the position modules that share one accounting:
 * calendars, pmcc and curve (`<prefix>positions`).
 *
 * Their ledgers agree on what every money column means. `gross_pnl` is mid-priced and cost-free,
 * option legs and delivered shares together; `fees` is the TOTAL of every cost the position
 * incurred -- entry fee, entry slippage, each traded exit's fee and slippage, and the settlement and
 * assignment charges (`settlement_fees`, a component of it, 2026-09-25). Fills are taken at mid and
 * slippage is CHARGED, so it is a cost column here and comes out of the total once, as settlement
 * does; net stays `gross_pnl - fees`, the rule core.ledgers holds for all three.
 *
 * Entry is the opening cash flow, signed (a debit structure is negative), and exit is derived as
 * gross − entry so each row adds up by construction: rolls, disposals and settlement payoffs all
 * land in exit.
 */

function round2(v: number): number {
  return Math.round(v * 100) / 100;
}

/** A positions column, or NULL on a ledger that predates it. */
export function positionCol(db: DatabaseHandle, table: string, name: string, alias = ""): string {
  return hasColumn(db, table, name) ? `${alias}${name}` : "NULL";
}

/**
 * The SELECT-list fragment every standard read needs beyond the module's own columns: the cost
 * parts, the settlement count and the assignments count. `table` is `<prefix>positions`.
 */
export function positionCashColumns(db: DatabaseHandle, prefix: string): string {
  const table = `${prefix}positions`;
  return `quantity, gross_pnl, fees, exit_reason, status,
          ${positionCol(db, table, "entry_slippage")} AS entry_slippage,
          ${positionCol(db, table, "exit_slippage")} AS exit_slippage,
          ${positionCol(db, table, "settlement_fees")} AS settlement_fees,
          ${positionCol(db, table, "itm_settlements")} AS itm_settlements,
          (SELECT COUNT(*) FROM ${prefix}assignments a WHERE a.position_id = ${table}.position_id) AS assigned`;
}

/**
 * How a position ended: with delivered shares (`assigned`), a leg settled in the money
 * (`settled`), everything expired worthless (`expired`), or traded out (`closed`). Null while open.
 */
export function positionExitKind(r: Record<string, unknown>): TradeMoney["exitKind"] {
  if (str(r["status"]) !== "closed") return null;
  if ((num(r["assigned"]) ?? 0) > 0) return "assigned";
  if ((num(r["itm_settlements"]) ?? 0) > 0) return "settled";
  if ((str(r["exit_reason"]) ?? "").includes("expire")) return "expired";
  return "closed";
}

/** The standard's money columns for one position. `entryPerShare` is signed: credit +, debit −. */
export function positionCash(r: Record<string, unknown>, entryPerShare: number | null): TradeMoney {
  const quantity = num(r["quantity"]);
  const gross = num(r["gross_pnl"]);
  const total = num(r["fees"]);
  const settlementFees = num(r["settlement_fees"]);
  const slips = [num(r["entry_slippage"]), num(r["exit_slippage"])];
  const slippage = slips[0] === null && slips[1] === null ? null : round2((slips[0] ?? 0) + (slips[1] ?? 0));
  const entryCash = entryPerShare === null ? null : round2(entryPerShare * 100 * (quantity ?? 1));
  const closed = str(r["status"]) === "closed" && gross !== null;
  return {
    quantity,
    price: entryPerShare,
    entryCash,
    exitCash: closed && entryCash !== null ? round2(gross - entryCash) : null,
    exitKind: positionExitKind(r),
    grossPnl: gross,
    fees: total === null ? null : round2(total - (settlementFees ?? 0) - (slippage ?? 0)),
    settlementFees,
    slippage,
    // Null propagates: an unrecorded gross or fee is not a zero-cost trade.
    netPnl: gross === null || total === null ? null : round2(gross - total),
  };
}

export const EMPTY_TRADE_TOTALS: TradeTotals = { positions: 0, gross: 0, fees: 0, settlementFees: 0, slippage: 0, net: 0 };

/** Totals through the same arithmetic as the rows, so the chip and the table cannot disagree. */
export function tradeTotals(rows: TradeMoney[]): TradeTotals {
  return rows.reduce<TradeTotals>(
    (t, c) => ({
      positions: t.positions + 1,
      gross: round2(t.gross + (c.grossPnl ?? 0)),
      fees: round2(t.fees + (c.fees ?? 0)),
      settlementFees: round2(t.settlementFees + (c.settlementFees ?? 0)),
      slippage: round2(t.slippage + (c.slippage ?? 0)),
      net: round2(t.net + (c.netPnl ?? 0)),
    }),
    EMPTY_TRADE_TOTALS,
  );
}
