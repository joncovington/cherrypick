import { useState } from "react";
import { useBwbHistory, useBwbMeta } from "../../lib/api";
import { DataCard, PnlCell, fmtMoney } from "../../components/DataTable";
import { Pager, ScopeSelect, usePage } from "../../components/ScopeBar";
import { fmtStrike } from "../../lib/optionFormat";

export function HistoryTab() {
  const [book, setBook] = useState<string | null>(null);
  const [symbol, setSymbol] = useState<string | null>(null);
  const meta = useBwbMeta();
  const { page, setOffset, setLimit } = usePage([book, symbol]);
  const { data, isLoading, isError, isPlaceholderData } = useBwbHistory({ book, symbol }, page);
  const rows = data?.rows ?? [];

  return (
    <div className="cards cards-wide">
      <DataCard
        title="completed positions"
        className="view-fade"
        headers={["entry -> close", "symbol", "book", "near/body x2/far", "entry credit", "add-on", "exit reason", "net", "fees"]}
        loading={isLoading}
        isError={isError}
        busy={isPlaceholderData}
        rowCount={rows.length}
        numFrom={4}
        empty="no completed positions yet -- bwb was built 2026-08-23 and has no paper data on this machine yet"
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
              {fmtStrike(r.nearStrike)}
              <span className="muted"> / </span>
              {fmtStrike(r.bodyStrike)}x2
              <span className="muted"> / </span>
              {fmtStrike(r.farStrike)}
            </td>
            <td>{fmtMoney(r.entryCredit)}</td>
            <td>
              {r.addonFiredAt === null ? (
                <span className="muted">never fired</span>
              ) : (
                <span className="chip chip-warn integrity-chip">{fmtMoney(r.addonCredit)}</span>
              )}
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
