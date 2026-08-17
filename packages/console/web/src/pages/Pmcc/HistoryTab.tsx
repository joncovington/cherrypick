import { Fragment, useState } from "react";
import type { PmccCycleRow } from "@console/shared";
import { usePmccAssignments, usePmccHistory, usePmccMeta } from "../../lib/api";
import { Card, DataCard, PnlCell, fmtMoney, fmtNum, fmtPct } from "../../components/DataTable";
import { Pager, ScopeSelect, usePage } from "../../components/ScopeBar";

/**
 * How a short leg left the book.
 *
 * Only `assigned` is coloured. It is the one close that hands over shares and carries the weekend
 * exposure, so it is the one worth catching the eye; a `traded` close is the strategy working
 * exactly as designed and gets the quiet treatment. (The house `chip-missing` is red — using it
 * here painted every ordinary close as a failure.)
 */
function closeKindChip(kind: string | null) {
  if (kind === null) return null;
  const cls = kind === "assigned" ? "chip-warn" : "pmcc-chip-quiet";
  const title =
    kind === "assigned"
      ? "Finished ITM under physical settlement: 100 short shares per contract delivered at the settlement print."
      : kind === "rolled"
        ? "Closed by a roll — the next short in the chain replaced it."
        : kind === "expired"
          ? "Expired worthless."
          : kind === "cash_settled"
            ? "Cash-settled at expiry."
            : "Closed by a trade.";
  return (
    <span className={`chip ${cls} pmcc-chip`} title={title}>
      {kind}
    </span>
  );
}

/**
 * The short chain: every short this cycle sold, in the order it sold them.
 *
 * A rolled position's story is the chain, not the final strike — "77 → 74 → 71" says the short was
 * chased down twice, which a single `short_strike` column would have quietly overwritten.
 */
function ShortChain({ row }: { row: PmccCycleRow }) {
  if (row.shorts.length === 0) return <span className="muted">—</span>;
  const last = row.shorts[row.shorts.length - 1]!;
  return (
    <span>
      {row.shorts.map((s, i) => (
        <span key={s.legRole}>
          {i > 0 && <span className="muted"> → </span>}
          {fmtNum(s.strike, 0)}
        </span>
      ))}{" "}
      {closeKindChip(last.closeKind)}
    </span>
  );
}

function CycleDetail({ row }: { row: PmccCycleRow }) {
  return (
    <tr className="pmcc-detail-row">
      <td colSpan={10}>
        <div className="pmcc-detail">
          <section>
            <h4>legs</h4>
            <p>
              <span className="muted">long</span> {fmtNum(row.longStrike, 0)}
              {row.longExpiration !== null && <span className="muted"> @ {row.longExpiration}</span>} ·{" "}
              <span className="muted">entry spot</span> {fmtNum(row.entrySpot, 2)}
              {row.settlementSpot !== null && (
                <>
                  {" "}
                  · <span className="muted">settlement spot</span> {fmtNum(row.settlementSpot, 2)}
                </>
              )}
            </p>
            <ul className="pmcc-plain-list">
              {row.shorts.map((s) => (
                <li key={s.legRole}>
                  <span className="mono">{s.legRole}</span> {fmtNum(s.strike, 0)}
                  {s.expiration !== null && <span className="muted"> @ {s.expiration}</span>} ·{" "}
                  <span className="muted">closed at</span> {fmtMoney(s.closeValue)} {closeKindChip(s.closeKind)}
                </li>
              ))}
            </ul>
          </section>

          {row.rolls.length > 0 && (
            <section>
              <h4>rolls</h4>
              <ul className="pmcc-plain-list">
                {row.rolls.map((r, i) => (
                  <li key={`${r.session ?? "roll"}-${String(i)}`}>
                    {r.session} · {fmtNum(r.oldStrike, 0)} → {fmtNum(r.newStrike, 0)}
                    {r.newExpiration !== null && <span className="muted"> @ {r.newExpiration}</span>} ·{" "}
                    <span className="muted">net credit</span> {fmtMoney(r.netRollCredit)}
                  </li>
                ))}
              </ul>
            </section>
          )}

          {row.assignments.length > 0 && (
            <section>
              <h4>delivered shares</h4>
              <ul className="pmcc-plain-list">
                {row.assignments.map((a) => (
                  <li key={a.legRole}>
                    {a.direction} {a.shares} shares @ {fmtNum(a.basis, 2)}{" "}
                    <span className="muted" title="Shares are booked at the SETTLEMENT SPOT, not the strike — the decomposition that keeps the option accounting untouched.">
                      (basis is the settlement print, not the {fmtNum(a.strike, 0)} strike)
                    </span>
                    {a.disposedSession === null ? (
                      <span className="chip chip-warn pmcc-chip">outstanding</span>
                    ) : (
                      <>
                        {" "}
                        · <span className="muted">disposed</span> {a.disposedSession} @ {fmtNum(a.disposalPrice, 2)} ·{" "}
                        <PnlCell v={a.sharePnl} />
                      </>
                    )}
                  </li>
                ))}
              </ul>
            </section>
          )}

          <section>
            <h4>result</h4>
            <p>
              <span className="muted">gross</span> {fmtMoney(row.grossPnl)} · <span className="muted">fees</span>{" "}
              {fmtMoney(row.fees)} · <span className="muted">net</span> <PnlCell v={row.netPnl} />
            </p>
            <p className="muted">
              Fees are the total modeled stack — entry, exit, every roll, and the settlement event — so net is one
              subtraction.
            </p>
          </section>
        </div>
      </td>
    </tr>
  );
}

export function HistoryTab() {
  const [book, setBook] = useState<string | null>(null);
  const [symbol, setSymbol] = useState<string | null>(null);
  const [open, setOpen] = useState<string | null>(null);
  const meta = usePmccMeta();
  const { page, setOffset, setLimit } = usePage([book, symbol]);
  const { data, isLoading, isError, isPlaceholderData } = usePmccHistory({ book, symbol }, page);
  const assignments = usePmccAssignments();
  const rows = data?.rows ?? [];
  const outstanding = assignments.data?.rows ?? [];

  return (
    <div className="cards cards-wide">
      <DataCard
        title="completed cycles"
        className="view-fade"
        headers={[
          "",
          "entry → close",
          "symbol",
          "book",
          "long",
          "short chain",
          "entry yield",
          "exit reason",
          "net",
          "fees",
        ]}
        loading={isLoading}
        isError={isError}
        busy={isPlaceholderData}
        rowCount={rows.length}
        numFrom={6}
        empty="no completed cycles yet — the module has been live since 2026-08-16"
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
          <Fragment key={r.positionId}>
            <tr
              onClick={() => setOpen(open === r.positionId ? null : r.positionId)}
              style={{ cursor: "pointer" }}
              title="click for legs, rolls and settlement detail"
            >
              <td>{open === r.positionId ? "▾" : "▸"}</td>
              <td>
                {r.entrySession}
                <span className="muted"> → {r.closedSession ?? "—"}</span>
              </td>
              <td>{r.symbol}</td>
              <td>{r.book}</td>
              <td>{fmtNum(r.longStrike, 0)}</td>
              <td>
                <ShortChain row={r} />
              </td>
              <td>{fmtPct(r.entryWeeklyYieldPct === null ? null : r.entryWeeklyYieldPct * 100, 2)}</td>
              <td>
                {r.status === "short_settled" ? (
                  <span
                    className="chip chip-warn pmcc-chip"
                    title="The short settled ITM; delivered shares are covered next session together with the long's sale. The result is not final until then."
                  >
                    awaiting disposal
                  </span>
                ) : (
                  (r.exitReason ?? <span className="muted">—</span>)
                )}
              </td>
              <td>
                {r.status === "short_settled" ? (
                  <span className="muted" title="pending next-session disposal">
                    —
                  </span>
                ) : (
                  <PnlCell v={r.netPnl} />
                )}
              </td>
              <td>{fmtMoney(r.fees)}</td>
            </tr>
            {open === r.positionId && <CycleDetail row={r} />}
          </Fragment>
        ))}
      </DataCard>

      <Card title="delivered shares" collapseKey="pmcc-assignments">
        {outstanding.length === 0 ? (
          <p className="muted">
            no assignments recorded — physical settlement writes rows here when a short finishes ITM
          </p>
        ) : (
          <div className="table-scroll">
            <table className="data-table num-from-3">
              <thead>
                <tr>
                  <th>assigned</th>
                  <th>symbol</th>
                  <th>position</th>
                  <th>shares</th>
                  <th>basis</th>
                  <th>disposed</th>
                  <th>share P&L</th>
                </tr>
              </thead>
              <tbody>
                {outstanding.map((a) => (
                  <tr key={`${a.positionId}-${a.legRole}`}>
                    <td>{a.assignedSession}</td>
                    <td>{a.symbol}</td>
                    <td className="mono">{a.legRole}</td>
                    <td>
                      {a.direction} {a.shares}
                    </td>
                    <td>{fmtNum(a.basis, 2)}</td>
                    <td>
                      {a.disposedSession ?? (
                        <span className="chip chip-warn pmcc-chip" title="Shares still outstanding — a Friday assignment carries them across the weekend.">
                          outstanding
                        </span>
                      )}
                    </td>
                    <td>
                      <PnlCell v={a.sharePnl} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </div>
  );
}
