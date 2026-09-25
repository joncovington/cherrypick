import type { EarningsPayload, EarningsTradeRow } from "@console/shared";
import { PnlCell, fmtMoney, fmtNum } from "../../components/DataTable";
import { Pager } from "../../components/ScopeBar";
import { PaperLiveBadge } from "../../components/shell/PaperLiveBadge";
import { HistoryTable } from "../../components/table/HistoryTable";
import type { ColumnDef } from "../../components/table/columns";
import { fmtCash, fmtPrice } from "../../lib/format";

/**
 * The closed trades of both books, in the suite's standard layout. earnings takes fills at mid and
 * CHARGES slippage as a cost, so `slip` is subtracted. The date range bounds the close: a result
 * belongs to the day it was realised.
 */
const COLUMNS: ColumnDef<EarningsTradeRow>[] = [
  { id: "book", header: "book", title: "paper or live", kind: "describe", render: (r) => <PaperLiveBadge mode={r.mode} /> },
  { id: "opened", header: "opened", kind: "describe", render: (r) => r.openedAt?.slice(0, 10) ?? "—" },
  { id: "closed", header: "closed", kind: "describe", className: "muted", render: (r) => r.closedAt?.slice(0, 10) ?? "—" },
  { id: "sym", header: "sym", kind: "describe", render: (r) => r.symbol },
  { id: "strategy", header: "strategy", kind: "describe", render: (r) => r.strategy },
  { id: "qty", header: "qty", kind: "describe", numeric: true, render: (r) => fmtNum(r.quantity, 0) },
  {
    id: "price",
    header: "price",
    title: "net entry price per share — cr received, db paid",
    kind: "describe",
    numeric: true,
    render: (r) => fmtPrice(r.entryCredit),
  },
  { id: "how", header: "how", kind: "describe", className: "muted", render: (r) => r.exitKind ?? "—" },
  { id: "reason", header: "exit reason", kind: "describe", className: "muted", render: (r) => r.exitReason ?? "—" },
  {
    id: "entry",
    header: "entry",
    title: "opening cash flow, whole position: + received, − paid",
    kind: "money",
    render: (r) => fmtCash(r.entryCash),
  },
  { id: "exit", header: "exit", title: "the close or the settlement, whole position", kind: "money", render: (r) => fmtCash(r.exitCash) },
  { id: "gross", header: "gross", title: "entry + exit, before any cost", kind: "money", render: (r) => fmtMoney(r.gross) },
  {
    id: "fees",
    header: "fees",
    title: "commissions and exchange fees",
    kind: "money",
    className: "muted",
    render: (r) => (
      <span title={r.settlementFees === null ? "includes any settlement: its share was not recorded" : undefined}>{fmtMoney(r.fees)}</span>
    ),
  },
  {
    id: "settle",
    header: "settle",
    title: "exercise / assignment fees at settlement",
    kind: "money",
    className: "muted",
    render: (r) => (r.settlementFees === null ? "n/r" : fmtMoney(r.settlementFees)),
  },
  {
    id: "slip",
    header: "slip",
    title: "charged as a cost in earnings, and subtracted",
    kind: "money",
    className: "muted",
    render: (r) => fmtMoney(r.slippage),
  },
  { id: "net", header: "net", title: "gross − fees − settlement − slippage", kind: "money", pinned: true, render: (r) => <PnlCell v={r.net} /> },
];

export function EarningsHistory({
  data,
  loading,
  isError,
  busy,
  ranged,
  page,
}: {
  data: EarningsPayload | undefined;
  loading: boolean;
  isError: boolean;
  busy: boolean;
  /** A date range is set, so an empty table means "nothing closed then", not "nothing yet". */
  ranged: boolean;
  page: { offset: number; limit: number; setOffset: (n: number) => void; setLimit: (n: number) => void };
}) {
  const t = data?.totals;
  const total = data?.trades.total ?? 0;
  return (
    <HistoryTable
      table="earnings-history"
      title={`History — ${total.toLocaleString()} closed across both books`}
      defs={COLUMNS}
      rows={data?.trades.rows ?? []}
      rowKey={(r) => `${r.mode}-${r.orderId}`}
      loading={loading}
      isError={isError}
      busy={busy}
      empty={ranged ? "nothing closed in this date range" : "nothing closed in this era yet"}
      dateBasis="closed"
      allTitle="every close in the era picked in the header"
      footer={
        total > 0 && (
          <>
            {t !== undefined && t.trades > 0 && (
              <span
                className="chip"
                title="Over every closed trade matching these filters, both books — not just this page. Gross − fees − settlement − slippage = net: earnings charges slippage as a cost."
              >
                net <PnlCell v={t.net} /> · gross {fmtMoney(t.gross)} · fees {fmtMoney(t.fees)} · settlement{" "}
                {fmtMoney(t.settlementFees)} · slippage {fmtMoney(t.slippage)}
              </span>
            )}
            <Pager
              offset={data?.trades.offset ?? page.offset}
              limit={data?.trades.limit ?? page.limit}
              total={total}
              onOffset={page.setOffset}
              onLimit={page.setLimit}
            />
          </>
        )
      }
    />
  );
}
