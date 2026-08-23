import { useState } from "react";
import { useCurveHistory, useCurveMeta } from "../../lib/api";
import { DataCard, PnlCell, fmtMoney, fmtNum } from "../../components/DataTable";
import { Pager, ScopeSelect, usePage } from "../../components/ScopeBar";
import { fmtStrike } from "../../lib/optionFormat";

export function HistoryTab() {
  const [book, setBook] = useState<string | null>(null);
  const [symbol, setSymbol] = useState<string | null>(null);
  const meta = useCurveMeta();
  const { page, setOffset, setLimit } = usePage([book, symbol]);
  const { data, isLoading, isError, isPlaceholderData } = useCurveHistory({ book, symbol }, page);
  const rows = data?.rows ?? [];

  return (
    <div className="cards cards-wide">
      <DataCard
        title="completed cycles"
        className="view-fade"
        headers={[
          "entry -> close",
          "symbol",
          "book",
          "short/long",
          "entry credit",
          "entry ratio/regime",
          "exit reason",
          "net",
          "fees",
        ]}
        loading={isLoading}
        isError={isError}
        busy={isPlaceholderData}
        rowCount={rows.length}
        numFrom={4}
        empty="no completed cycles yet -- curve was built 2026-08-22 and has no paper data on this machine yet"
        updatedAt={data === undefined ? undefined : Date.now()}
        controls={
          <>
            <ScopeSelect label="book filter" value={book} options={meta.data?.books} onChange={setBook} allLabel="all books" />
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
            <Pager offset={data.offset} limit={data.limit} total={data.total} onOffset={setOffset} onLimit={setLimit} />
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
            <td>{r.book}</td>
            <td>
              {fmtStrike(r.shortStrike)}
              <span className="muted"> / </span>
              {fmtStrike(r.longStrike)}
            </td>
            <td>{fmtMoney(r.entryCredit)}</td>
            <td>
              {fmtNum(r.entryRatio, 3)}
              {r.entryRegime !== null && <span className="muted"> ({r.entryRegime})</span>}
              {r.entryHook && <span className="chip chip-warn integrity-chip">hook</span>}
            </td>
            <td>{r.exitReason ?? <span className="muted">—</span>}</td>
            <td>
              <PnlCell v={r.netPnl} />
            </td>
            <td>{fmtMoney(r.fees)}</td>
          </tr>
        ))}
      </DataCard>
    </div>
  );
}
