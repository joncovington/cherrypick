import { useState } from "react";
import { useBwbHistory, useBwbMeta } from "../../lib/api";
import { DataCard, PnlCell, fmtMoney } from "../../components/DataTable";
import { Pager, ScopeSelect, usePage } from "../../components/ScopeBar";
import { fmtStrike } from "../../lib/optionFormat";
import { fmtCash, fmtPrice } from "../../lib/format";

/**
 * Completed positions in the suite's standard trade layout (root CLAUDE.md): entry and exit as
 * signed whole-position cash, how each ended, gross, then every cost, then net.
 *
 * bwb prices fills at mid and CHARGES slippage as a cost, so `slip` is subtracted here -- the other
 * of the suite's two slippage models from flies and meic, and the column's title says so.
 */
export function HistoryTab() {
  const [arm, setBook] = useState<string | null>(null);
  const [symbol, setSymbol] = useState<string | null>(null);
  const meta = useBwbMeta();
  const { page, setOffset, setLimit } = usePage([arm, symbol]);
  const { data, isLoading, isError, isPlaceholderData } = useBwbHistory({ arm, symbol }, page);
  const rows = data?.rows ?? [];
  const t = data?.totals;

  return (
    <div className="cards cards-wide">
      <DataCard
        title="completed positions"
        className="view-fade"
        headers={["entry -> close", "symbol", "arm", "near/body x2/far", "add-on", "qty", "price", "entry", "exit", "how", "gross", "fees", "settle", "slip", "net", "exit reason"]}
        loading={isLoading}
        isError={isError}
        busy={isPlaceholderData}
        rowCount={rows.length}
        numFrom={5}
        empty="no completed positions yet -- results fill in as the daily ladder settles at expiry"
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
              {fmtStrike(r.nearStrike)}
              <span className="muted"> / </span>
              {fmtStrike(r.bodyStrike)}x2
              <span className="muted"> / </span>
              {fmtStrike(r.farStrike)}
            </td>
            <td>
              {r.addonFiredAt === null ? (
                <span className="muted">never fired</span>
              ) : (
                <span className="chip chip-warn integrity-chip" title="the add-on's own credit, per share">
                  {fmtPrice(r.addonCredit)}
                </span>
              )}
            </td>
            <td>{r.quantity ?? "—"}</td>
            <td title="the fly's own net credit, per share">{fmtPrice(r.entryCredit)}</td>
            <td>{fmtCash(r.entryCash)}</td>
            <td>{fmtCash(r.exitCash)}</td>
            <td className="muted">{r.exitKind ?? "—"}</td>
            <td>{fmtMoney(r.grossPnl)}</td>
            <td className="muted" title={r.settlementFees === null ? "includes settlement: its share was not recorded" : undefined}>
              {fmtMoney(r.fees)}
            </td>
            <td className="muted">{r.settlementFees === null ? "n/r" : fmtMoney(r.settlementFees)}</td>
            <td className="muted" title="charged as a cost in bwb, and subtracted">{fmtMoney(r.slippage)}</td>
            <td>
              <PnlCell v={r.netPnl} />
            </td>
            <td className="muted">{r.exitReason ?? "—"}</td>
          </tr>
        ))}
      </DataCard>
    </div>
  );
}
