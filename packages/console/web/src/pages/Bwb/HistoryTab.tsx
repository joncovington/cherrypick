import { useState } from "react";
import type { BwbCycleRow } from "@console/shared";
import { useBwbHistory, useBwbMeta } from "../../lib/api";
import { PnlCell, fmtMoney } from "../../components/DataTable";
import { Pager, ScopeSelect, usePage } from "../../components/ScopeBar";
import { HistoryTable } from "../../components/table/HistoryTable";
import { useUrlDateRange } from "../../components/table/DateRange";
import type { ColumnDef } from "../../components/table/columns";
import { fmtStrike } from "../../lib/optionFormat";
import { fmtCash, fmtPrice } from "../../lib/format";

/**
 * Completed positions in the suite's standard trade layout (root CLAUDE.md): entry and exit as
 * signed whole-position cash, how each ended, gross, then every cost, then net.
 *
 * bwb prices fills at mid and CHARGES slippage as a cost, so `slip` is subtracted here -- the other
 * of the suite's two slippage models from flies and meic, and the column's title says so.
 */
const COLUMNS: ColumnDef<BwbCycleRow>[] = [
  {
    id: "dates",
    header: "entry → close",
    kind: "describe",
    render: (r) => (
      <>
        {r.entrySession}
        <span className="muted"> → {r.closedSession ?? "—"}</span>
      </>
    ),
  },
  { id: "symbol", header: "symbol", kind: "describe", render: (r) => r.symbol },
  { id: "arm", header: "arm", kind: "describe", render: (r) => r.arm },
  {
    id: "strikes",
    header: "near/body x2/far",
    kind: "describe",
    numeric: true,
    render: (r) => (
      <>
        {fmtStrike(r.nearStrike)}
        <span className="muted"> / </span>
        {fmtStrike(r.bodyStrike)}x2
        <span className="muted"> / </span>
        {fmtStrike(r.farStrike)}
      </>
    ),
  },
  {
    id: "addon",
    header: "add-on",
    title: "the add-on's own credit, per share, when its trigger fired",
    kind: "describe",
    render: (r) =>
      r.addonFiredAt === null ? (
        <span className="muted">never fired</span>
      ) : (
        <span className="chip chip-warn integrity-chip" title="the add-on's own credit, per share">
          {fmtPrice(r.addonCredit)}
        </span>
      ),
  },
  { id: "qty", header: "qty", kind: "describe", numeric: true, render: (r) => r.quantity ?? "—" },
  {
    id: "price",
    header: "price",
    title: "the fly's own net credit, per share",
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
  { id: "gross", header: "gross", title: "entry + exit, before any cost", kind: "money", render: (r) => fmtMoney(r.grossPnl) },
  {
    id: "fees",
    header: "fees",
    title: "commissions and exchange fees",
    kind: "money",
    className: "muted",
    render: (r) => (
      <span title={r.settlementFees === null ? "includes settlement: its share was not recorded" : undefined}>{fmtMoney(r.fees)}</span>
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
    title: "charged as a cost in bwb, and subtracted",
    kind: "money",
    className: "muted",
    render: (r) => fmtMoney(r.slippage),
  },
  { id: "net", header: "net", title: "gross − fees − settlement − slippage", kind: "money", pinned: true, render: (r) => <PnlCell v={r.netPnl} /> },
];

export function HistoryTab() {
  const [arm, setBook] = useState<string | null>(null);
  const [symbol, setSymbol] = useState<string | null>(null);
  const { from, to } = useUrlDateRange();
  const meta = useBwbMeta();
  const { page, setOffset, setLimit } = usePage([arm, symbol, from, to]);
  const { data, isLoading, isError, isPlaceholderData } = useBwbHistory({ arm, symbol, from, to }, page);
  const t = data?.totals;

  return (
    <div className="cards cards-wide">
      <HistoryTable
        table="bwb-history"
        title="completed positions"
        className="view-fade"
        defs={COLUMNS}
        rows={data?.rows ?? []}
        rowKey={(r) => r.positionId}
        loading={isLoading}
        isError={isError}
        busy={isPlaceholderData}
        empty={
          from !== null || to !== null
            ? "no position closed in this date range"
            : "no completed positions yet -- results fill in as the daily ladder settles at expiry"
        }
        updatedAt={data === undefined ? undefined : Date.now()}
        dateBasis="closed"
        filters={
          <>
            <ScopeSelect label="arm filter" value={arm} options={meta.data?.arms} onChange={setBook} allLabel="all arms" />
            <ScopeSelect
              label="symbol filter"
              value={symbol}
              options={meta.data?.symbols}
              onChange={setSymbol}
              allLabel="all symbols"
            />
          </>
        }
        footer={
          data !== undefined && (
            <>
              {t !== undefined && t.positions > 0 && (
                <span
                  className="chip"
                  title="Over every completed position matching these filters — not just this page. Gross − fees − settlement − slippage = net: bwb charges slippage as a cost."
                >
                  net <PnlCell v={t.net} /> · gross {fmtMoney(t.gross)} · fees {fmtMoney(t.fees)} · settlement{" "}
                  {fmtMoney(t.settlementFees)} · slippage {fmtMoney(t.slippage)}
                </span>
              )}
              <Pager offset={data.offset} limit={data.limit} total={data.total} onOffset={setOffset} onLimit={setLimit} />
            </>
          )
        }
      />
    </div>
  );
}
