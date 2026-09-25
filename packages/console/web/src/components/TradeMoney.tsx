import type { TradeMoney, TradeTotals } from "@console/shared";
import { PnlCell } from "./DataTable";
import type { ColumnDef } from "./table/columns";
import { fmtCash, fmtMoney, fmtPrice } from "../lib/format";

/**
 * The trade table standard's columns (root CLAUDE.md, "Trade histories and reports"), for the
 * modules whose readers hand back a `TradeMoney` row: calendars, pmcc and curve. Declared once, so
 * the headers and their definitions are stated in one place, in the same order, with the same
 * titles -- and as `ColumnDef`s, so the history tables' columns menu can hide and reorder them.
 *
 * qty, price and how say which trade a row is (describe columns); entry through net are the row
 * adding up (the money block, net pinned last).
 *
 * These modules take fills at mid and CHARGE slippage as a cost, so `slip` is subtracted; its title
 * says so. flies and meic are the other model (slippage inside the fill price) and declare their own.
 */
export function tradeMoneyColumns<R extends TradeMoney>(priceTitle: string): {
  describe: ColumnDef<R>[];
  money: ColumnDef<R>[];
} {
  return {
    describe: [
      { id: "qty", header: "qty", kind: "describe", numeric: true, render: (r) => r.quantity ?? "—" },
      { id: "price", header: "price", title: priceTitle, kind: "describe", numeric: true, render: (r) => fmtPrice(r.price) },
      { id: "how", header: "how", kind: "describe", className: "muted", render: (r) => r.exitKind ?? "—" },
    ],
    money: [
      {
        id: "entry",
        header: "entry",
        title: "opening cash flow, whole position: + received, − paid",
        kind: "money",
        render: (r) => fmtCash(r.entryCash),
      },
      {
        id: "exit",
        header: "exit",
        title: "every later cash flow — closes, rolls, share disposals, settlement — whole position",
        kind: "money",
        render: (r) => fmtCash(r.exitCash),
      },
      { id: "gross", header: "gross", title: "entry + exit, before any cost", kind: "money", render: (r) => fmtMoney(r.grossPnl) },
      {
        id: "fees",
        header: "fees",
        title: "commissions and exchange fees",
        kind: "money",
        className: "muted",
        render: (r) => (
          <span title={r.settlementFees === null ? "includes settlement: its share was not recorded" : undefined}>
            {fmtMoney(r.fees)}
          </span>
        ),
      },
      {
        id: "settle",
        header: "settle",
        title: "exercise, assignment and share-delivery fees",
        kind: "money",
        className: "muted",
        render: (r) => (r.settlementFees === null ? (r.exitKind === null ? "—" : "n/r") : fmtMoney(r.settlementFees)),
      },
      {
        id: "slip",
        header: "slip",
        title: "charged as a cost in this module, and subtracted",
        kind: "money",
        className: "muted",
        render: (r) => fmtMoney(r.slippage),
      },
      {
        id: "net",
        header: "net",
        title: "gross − fees − settlement − slippage",
        kind: "money",
        pinned: true,
        render: (r) => <PnlCell v={r.netPnl} />,
      },
    ],
  };
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
