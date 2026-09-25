import type { TradeMoney, TradeTotals } from "@console/shared";
import { PnlCell } from "./DataTable";
import { fmtCash, fmtMoney, fmtPrice } from "../lib/format";

/**
 * The trade table standard's money columns (root CLAUDE.md, "Trade histories and reports"), for the
 * modules whose readers hand back a `TradeMoney` row: calendars, pmcc and curve. One component so
 * the ten headers and their definitions are stated once, in the same order, with the same titles.
 *
 * These modules take fills at mid and CHARGE slippage as a cost, so `slip` is subtracted; its
 * header says so. flies and meic are the other model (slippage inside the fill price) and render
 * their own cells.
 */

export const TRADE_MONEY_HEADERS = ["qty", "price", "entry", "exit", "how", "gross", "fees", "settle", "slip", "net"];

export function TradeMoneyCells({ row, priceTitle }: { row: TradeMoney; priceTitle?: string }) {
  return (
    <>
      <td>{row.quantity ?? "—"}</td>
      <td title={priceTitle ?? "net entry price per share: cr received, db paid"}>{fmtPrice(row.price)}</td>
      <td title="opening cash flow, whole position: + received, − paid">{fmtCash(row.entryCash)}</td>
      <td title="every later cash flow — closes, rolls, share disposals, settlement — whole position">{fmtCash(row.exitCash)}</td>
      <td className="muted">{row.exitKind ?? "—"}</td>
      <td title="entry + exit, before any cost">{fmtMoney(row.grossPnl)}</td>
      <td className="muted" title={row.settlementFees === null ? "includes settlement: its share was not recorded" : "commissions and exchange fees"}>
        {fmtMoney(row.fees)}
      </td>
      <td className="muted" title="exercise, assignment and share-delivery fees">
        {row.settlementFees === null ? (row.exitKind === null ? "—" : "n/r") : fmtMoney(row.settlementFees)}
      </td>
      <td className="muted" title="charged as a cost in this module, and subtracted">{fmtMoney(row.slippage)}</td>
      <td>
        <PnlCell v={row.netPnl} />
      </td>
    </>
  );
}

export function TradeTotalsChip({ totals, noun = "positions" }: { totals: TradeTotals | undefined; noun?: string }) {
  if (totals === undefined || totals.positions === 0) return null;
  return (
    <span
      className="chip"
      title={`Over every ${noun.replace(/s$/, "")} matching these filters — not just this page. Gross − fees − settlement − slippage = net: this module charges slippage as a cost.`}
    >
      net <PnlCell v={totals.net} /> · {totals.positions.toLocaleString()} {noun} · gross {fmtMoney(totals.gross)} · fees{" "}
      {fmtMoney(totals.fees)} · settlement {fmtMoney(totals.settlementFees)} · slippage {fmtMoney(totals.slippage)}
    </span>
  );
}
