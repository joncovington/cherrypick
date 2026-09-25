import { useState } from "react";
import { useCurveHistory, useCurveMeta } from "../../lib/api";
import { DataCard, fmtNum } from "../../components/DataTable";
import { Pager, ScopeSelect, usePage } from "../../components/ScopeBar";
import { TRADE_MONEY_HEADERS, TradeMoneyCells, TradeTotalsChip } from "../../components/TradeMoney";
import { fmtStrike } from "../../lib/optionFormat";

/** Completed cycles in the suite's standard trade layout (root CLAUDE.md). */
export function HistoryTab() {
  const [arm, setBook] = useState<string | null>(null);
  const [symbol, setSymbol] = useState<string | null>(null);
  const meta = useCurveMeta();
  const { page, setOffset, setLimit } = usePage([arm, symbol]);
  const { data, isLoading, isError, isPlaceholderData } = useCurveHistory({ arm, symbol }, page);
  const rows = data?.rows ?? [];

  return (
    <div className="cards cards-wide">
      <DataCard
        title="completed cycles"
        className="view-fade"
        headers={["entry -> close", "symbol", "arm", "short/long", "entry ratio/regime", ...TRADE_MONEY_HEADERS, "exit reason"]}
        loading={isLoading}
        isError={isError}
        busy={isPlaceholderData}
        rowCount={rows.length}
        numFrom={5}
        empty="no completed cycles yet -- one position per arm at ~30-45 DTE, so the first closes a month in"
        updatedAt={data === undefined ? undefined : Date.now()}
        controls={
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
      >
        {rows.map((r) => (
          <tr key={r.positionId}>
            <td>
              {r.entrySession}
              <span className="muted"> -&gt; {r.closedSession ?? "—"}</span>
            </td>
            <td>{r.symbol}</td>
            <td>{r.arm}</td>
            <td>
              {fmtStrike(r.shortStrike)}
              <span className="muted"> / </span>
              {fmtStrike(r.longStrike)}
            </td>
            <td>
              {fmtNum(r.entryRatio, 3)}
              {r.entryRegime !== null && <span className="muted"> ({r.entryRegime})</span>}
              {r.entryHook && <span className="chip chip-warn integrity-chip">hook</span>}
            </td>
            <TradeMoneyCells row={r} priceTitle="the call credit spread's net credit, per share" />
            <td className="muted">{r.exitReason ?? "—"}</td>
          </tr>
        ))}
      </DataCard>
    </div>
  );
}
