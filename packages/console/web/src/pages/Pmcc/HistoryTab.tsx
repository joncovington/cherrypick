import { Fragment, useState } from "react";
import type { PmccCycleRow } from "@console/shared";
import { usePmccAssignments, usePmccHistory, usePmccMeta } from "../../lib/api";
import { Card, DataCard, PnlCell, fmtMoney, fmtNum, fmtPct } from "../../components/DataTable";
import { Pager, ScopeSelect, usePage } from "../../components/ScopeBar";
import { fmtStrike } from "../../lib/optionFormat";
import { EntrySpreadCell } from "./EntrySpread";

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
          {fmtStrike(s.strike)}
        </span>
      ))}{" "}
      {closeKindChip(last.closeKind)}
    </span>
  );
}

/**
 * The fee stack, split into what was paid and what was given away.
 *
 * Commissions are a price list; slippage is the market's width and is the thing a structure choice
 * can actually change. The first session closed four cycles whose fees were 98% slippage, and a
 * single "fees" figure made that look like an unremarkable cost of doing business rather than the
 * whole result. `sum(parts)` is checked against the recorded total instead of assumed: the module
 * bundles roll and settlement events into `fees` too, and any remainder belongs on screen, not
 * silently dropped.
 */
function FeeSplit({ row }: { row: PmccCycleRow }) {
  const parts = [row.entryCost, row.exitCost, row.entrySlippage, row.exitSlippage];
  if (parts.every((p) => p === null)) {
    return (
      <p className="muted">
        no cost breakdown recorded on this cycle — pre-instrumentation rows are not zero-cost ones
      </p>
    );
  }
  const commissions = (row.entryCost ?? 0) + (row.exitCost ?? 0);
  const slippage = (row.entrySlippage ?? 0) + (row.exitSlippage ?? 0);
  const accounted = commissions + slippage;
  const other = row.fees === null ? null : row.fees - accounted;
  const slipShare = row.fees !== null && row.fees > 0 ? (slippage / row.fees) * 100 : null;
  return (
    <>
      <p>
        <span className="muted">commissions</span> {fmtMoney(commissions)} ·{" "}
        <span className={slipShare !== null && slipShare > 50 ? "pmcc-warn" : ""}>
          <span className="muted">slippage</span> {fmtMoney(slippage)}
          {slipShare !== null && <> ({fmtPct(slipShare, 0)} of fees)</>}
        </span>
        {other !== null && Math.abs(other) >= 0.005 && (
          <>
            {" "}
            · <span className="muted">other events</span> {fmtMoney(other)}
          </>
        )}
      </p>
      <p className="muted">
        Entry {fmtMoney(row.entrySlippage)} + exit {fmtMoney(row.exitSlippage)} of slippage against{" "}
        {fmtMoney(row.entryNetTv === null ? null : row.entryNetTv * 100)} of time value the structure was sold
        to capture.
      </p>
    </>
  );
}

function CycleDetail({ row }: { row: PmccCycleRow }) {
  return (
    <tr className="pmcc-detail-row">
      <td colSpan={11}>
        <div className="pmcc-detail">
          <section>
            <h4>legs</h4>
            <p>
              <span className="muted">long</span> {fmtStrike(row.longStrike)}
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
                  <span className="mono">{s.legRole}</span> {fmtStrike(s.strike)}
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
                      (basis is the settlement print, not the {fmtStrike(a.strike)} strike)
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
            <FeeSplit row={row} />
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
          "entry spread",
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
              <td>{fmtStrike(r.longStrike)}</td>
              <td>
                <ShortChain row={r} />
              </td>
              <td>{fmtPct(r.entryWeeklyYieldPct === null ? null : r.entryWeeklyYieldPct * 100, 2)}</td>
              <td>
                <EntrySpreadCell pct={r.entryMaxSpreadPct} abs={r.entryMaxSpreadAbs} netTv={r.entryNetTv} />
              </td>
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
