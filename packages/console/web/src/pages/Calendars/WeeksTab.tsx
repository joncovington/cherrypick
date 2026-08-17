import { useState } from "react";
import type { CalendarsPayload } from "@console/shared";
import { useCalendarsWeek, useCalendarsWeeks } from "../../lib/api";
import { DataCard, fmtMoney, fmtNum, fmtPct, PnlCell } from "../../components/DataTable";

/**
 * Every week on file, per book, with the week's legs one click down.
 *
 * `closed` rides beside `positions` in every row because a week does not finish while its delivered
 * shares are outstanding — reporting the closed half's net under the week's name would read as the
 * week's result. An unfinished week gets an em-dash and a badge, not a number.
 */
function WeekDetail({ week }: { week: string }) {
  const { data, isLoading } = useCalendarsWeek(week);
  const rows = data?.rows ?? [];
  if (isLoading) return <p className="muted">loading…</p>;
  if (rows.length === 0) return <p className="muted">no positions on file for this week</p>;
  return (
    <div className="cal-detail">
      {rows.map((p) => (
        <div key={p.positionId}>
          <h4>
            <span className="mono">{p.book}</span> · {p.side} @ {fmtNum(p.strike, 0)}
            <span className="muted">
              {" "}
              · {p.status}
              {p.exitReason !== null && ` (${p.exitReason})`}
            </span>
          </h4>
          <table className="data-table num-from-2">
            <thead>
              <tr>
                <th>leg</th>
                <th>expiration</th>
                <th>action</th>
                <th>entry mid</th>
                <th>exit</th>
                <th>kind</th>
              </tr>
            </thead>
            <tbody>
              {p.legs.map((l) => (
                <tr key={l.legRole}>
                  <td>
                    <span className="mono">{l.legRole}</span>
                  </td>
                  <td className="mono">{l.expiration}</td>
                  <td>{l.action}</td>
                  <td>{fmtNum(l.entryMid, 2)}</td>
                  <td>{l.closeValue === null ? <span className="muted">—</span> : fmtNum(l.closeValue, 2)}</td>
                  <td>
                    <span className="muted">{l.closeKind ?? l.status}</span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="cal-note">
            entry debit {fmtNum(p.entryDebit, 2)} · spot {fmtNum(p.entrySpot, 2)} · EM {fmtNum(p.entryEm, 2)}
            {p.settlementSpot !== null && ` · settled ${fmtNum(p.settlementSpot, 2)}`}
            {p.itmSettlements !== null && p.itmSettlements > 0 && (
              <span className="cal-warn"> · {p.itmSettlements} ITM at expiry</span>
            )}
          </p>
        </div>
      ))}
    </div>
  );
}

export function WeeksTab({ data }: { data: CalendarsPayload | undefined }) {
  const { data: weeks, isLoading, isError, dataUpdatedAt } = useCalendarsWeeks();
  const [open, setOpen] = useState<string | null>(null);
  const rows = weeks?.rows ?? [];
  const em = data?.emVsRealized ?? [];

  return (
    <div className="cards cards-wide">
      <DataCard
        title="weeks"
        headers={["week", "structure", "book", "positions", "entry debit", "entry spot", "settled", "gross", "fees", "net"]}
        loading={isLoading}
        isError={isError}
        rowCount={rows.length}
        numFrom={3}
        empty="no week has been entered yet"
        updatedAt={dataUpdatedAt}
        className="view-fade"
      >
        {rows.map((r) => {
          const key = `${r.weekOf}-${r.book}`;
          const partial = r.closed < r.positions;
          return [
            <tr key={key} className="cal-row-click" onClick={() => { setOpen(open === key ? null : key); }}>
              <td className="mono">{r.weekOf}</td>
              <td className="mono">{r.structure}</td>
              <td className="mono">{r.book}</td>
              <td>
                {r.closed}/{r.positions}
                {partial && (
                  <span className="chip chip-warn cal-chip" title="A week does not close while any leg — or any delivered share position — is still outstanding.">
                    open
                  </span>
                )}
              </td>
              <td>{fmtNum(r.entryDebit, 2)}</td>
              <td>{fmtNum(r.entrySpot, 2)}</td>
              <td>{fmtNum(r.settlementSpot, 2)}</td>
              <td>{fmtMoney(r.grossPnl)}</td>
              <td>{fmtMoney(r.fees)}</td>
              <td>{r.netPnl === null ? <span className="muted">—</span> : <PnlCell v={r.netPnl} />}</td>
            </tr>,
            // The detail is the whole WEEK, every book of it, because the books are only
            // interesting against each other -- they share the entry, so a single book's legs in
            // isolation say nothing the row above does not.
            open === key ? (
              <tr key={`${key}-detail`} className="cal-detail-row">
                <td colSpan={10}>
                  <WeekDetail week={r.weekOf} />
                </td>
              </tr>
            ) : null,
          ];
        })}
      </DataCard>

      <DataCard
        title="expected move vs realized"
        headers={["week", "structure", "expected move", "realized", "ratio"]}
        loading={data === undefined}
        rowCount={em.length}
        numFrom={2}
        empty="no week has settled yet"
        footer={
          <p className="cal-note">
            The strategy&rsquo;s premise, measured: the expected move taken at entry against the move actually
            realized to the Friday expiration. Floats, not verdicts — a calendar wants the underlying to sit
            near its strike, so a ratio under 1 is the structure&rsquo;s friend and this table is the record of
            how often that happened, not an opinion about whether it will.
          </p>
        }
      >
        {em.map((r) => (
          <tr key={r.weekOf}>
            <td className="mono">{r.weekOf}</td>
            <td className="mono">{r.structure}</td>
            <td>{fmtNum(r.expectedMove, 2)}</td>
            <td>{fmtNum(r.realizedMove, 2)}</td>
            <td className={r.ratio !== null && r.ratio > 1 ? "cal-warn" : ""}>
              {r.ratio === null ? "—" : fmtPct(r.ratio * 100, 0)}
            </td>
          </tr>
        ))}
      </DataCard>
    </div>
  );
}
