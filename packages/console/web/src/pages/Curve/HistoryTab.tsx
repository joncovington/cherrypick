import { useState } from "react";
import type { CurveCycleRow } from "@console/shared";
import { useCurveHistory, useCurveMeta } from "../../lib/api";
import { fmtNum } from "../../components/DataTable";
import { Pager, ScopeSelect, usePage } from "../../components/ScopeBar";
import { TradeTotalsChip, tradeMoneyColumns } from "../../components/TradeMoney";
import { HistoryTable } from "../../components/table/HistoryTable";
import { useUrlDateRange } from "../../components/table/DateRange";
import type { ColumnDef } from "../../components/table/columns";
import { fmtStrike } from "../../lib/optionFormat";

const MONEY = tradeMoneyColumns<CurveCycleRow>("the call credit spread's net credit, per share");

const COLUMNS: ColumnDef<CurveCycleRow>[] = [
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
    header: "short/long",
    kind: "describe",
    numeric: true,
    render: (r) => (
      <>
        {fmtStrike(r.shortStrike)}
        <span className="muted"> / </span>
        {fmtStrike(r.longStrike)}
      </>
    ),
  },
  {
    id: "regime",
    header: "entry ratio/regime",
    kind: "describe",
    render: (r) => (
      <>
        {fmtNum(r.entryRatio, 3)}
        {r.entryRegime !== null && <span className="muted"> ({r.entryRegime})</span>}
        {r.entryHook && <span className="chip chip-warn integrity-chip">hook</span>}
      </>
    ),
  },
  ...MONEY.describe,
  { id: "reason", header: "exit reason", kind: "describe", className: "muted", render: (r) => r.exitReason ?? "—" },
  ...MONEY.money,
];

/** Completed cycles in the suite's standard trade layout (root CLAUDE.md). */
export function HistoryTab() {
  const [arm, setBook] = useState<string | null>(null);
  const [symbol, setSymbol] = useState<string | null>(null);
  const { from, to } = useUrlDateRange();
  const meta = useCurveMeta();
  const { page, setOffset, setLimit } = usePage([arm, symbol, from, to]);
  const { data, isLoading, isError, isPlaceholderData } = useCurveHistory({ arm, symbol, from, to }, page);

  return (
    <div className="cards cards-wide">
      <HistoryTable
        table="curve-history"
        title="completed cycles"
        className="view-fade"
        defs={COLUMNS}
        rows={data?.rows ?? []}
        rowKey={(r) => r.positionId}
        loading={isLoading}
        isError={isError}
        busy={isPlaceholderData}
        empty={
          from !== null || to !== null
            ? "no cycle closed in this date range"
            : "no completed cycles yet -- one position per arm at ~30-45 DTE, so the first closes a month in"
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
              <TradeTotalsChip totals={data.totals} noun="cycles" />
              <Pager offset={data.offset} limit={data.limit} total={data.total} onOffset={setOffset} onLimit={setLimit} />
            </>
          )
        }
      />
    </div>
  );
}
