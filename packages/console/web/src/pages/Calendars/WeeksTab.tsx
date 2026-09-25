import { useState } from "react";
import type { CalendarsPayload, CalendarsWeekRow } from "@console/shared";
import { useCalendarsWeek, useCalendarsWeeks } from "../../lib/api";
import { DataCard, fmtNum, fmtPct } from "../../components/DataTable";
import { TradeTotalsChip, tradeMoneyColumns } from "../../components/TradeMoney";
import { HistoryTable } from "../../components/table/HistoryTable";
import { useUrlDateRange } from "../../components/table/DateRange";
import type { ColumnDef } from "../../components/table/columns";

/**
 * Every week on file, per arm, with the week's legs one click down.
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
            <span className="mono">{p.arm}</span> · {p.side} @ {fmtNum(p.strike, 0)}
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
          <p className="integrity-note">
            entry debit {fmtNum(p.entryDebit, 2)} · spot {fmtNum(p.entrySpot, 2)} · EM {fmtNum(p.entryEm, 2)}
            {p.settlementSpot !== null && ` · settled ${fmtNum(p.settlementSpot, 2)}`}
            {p.itmSettlements !== null && p.itmSettlements > 0 && (
              <span className="integrity-warn"> · {p.itmSettlements} ITM at expiry</span>
            )}
          </p>
        </div>
      ))}
    </div>
  );
}

const MONEY = tradeMoneyColumns<CalendarsWeekRow>("the week's call and put calendar debits together, per share");

const COLUMNS: ColumnDef<CalendarsWeekRow>[] = [
  { id: "week", header: "week", kind: "describe", className: "mono", render: (r) => r.weekOf },
  { id: "structure", header: "structure", kind: "describe", className: "mono", render: (r) => r.structure },
  { id: "arm", header: "arm", kind: "describe", className: "mono", render: (r) => r.arm },
  {
    id: "positions",
    header: "positions",
    kind: "describe",
    numeric: true,
    render: (r) => (
      <>
        {r.closed}/{r.positions}
        {r.closed < r.positions && (
          <span className="chip chip-warn integrity-chip" title="A week does not close while any leg — or any delivered share position — is still outstanding.">
            open
          </span>
        )}
      </>
    ),
  },
  { id: "spot", header: "entry spot", kind: "describe", numeric: true, render: (r) => fmtNum(r.entrySpot, 2) },
  { id: "settled", header: "settled", kind: "describe", numeric: true, render: (r) => fmtNum(r.settlementSpot, 2) },
  ...MONEY.describe,
  ...MONEY.money,
];

export function WeeksTab({ data }: { data: CalendarsPayload | undefined }) {
  const { from, to } = useUrlDateRange();
  const { data: weeks, isLoading, isError, isPlaceholderData, dataUpdatedAt } = useCalendarsWeeks({ from, to });
  const [open, setOpen] = useState<string | null>(null);
  const em = data?.emVsRealized ?? [];
  const keyOf = (r: CalendarsWeekRow) => `${r.weekOf}-${r.arm}`;

  return (
    <div className="cards cards-wide">
      <HistoryTable
        table="calendars-weeks"
        title="weeks"
        className="view-fade"
        defs={COLUMNS}
        rows={weeks?.rows ?? []}
        rowKey={keyOf}
        loading={isLoading}
        isError={isError}
        busy={isPlaceholderData}
        empty={from !== null || to !== null ? "no week in this date range" : "no week has been entered yet"}
        updatedAt={dataUpdatedAt}
        dateBasis="week"
        allTitle="every week on file"
        footer={<TradeTotalsChip totals={weeks?.totals} noun="finished weeks" />}
        rowProps={(r) => ({
          className: "cal-row-click",
          onClick: () => {
            setOpen(open === keyOf(r) ? null : keyOf(r));
          },
        })}
        // The detail is the whole WEEK, every arm of it, because the arms are only interesting
        // against each other -- they share the entry, so a single arm's legs in isolation say
        // nothing the row above does not.
        expanded={(r) => (open === keyOf(r) ? <WeekDetail week={r.weekOf} /> : null)}
        detailClassName="cal-detail-row"
      />

      <DataCard
        title="expected move vs realized"
        headers={["week", "structure", "expected move", "realized", "ratio"]}
        loading={data === undefined}
        rowCount={em.length}
        numFrom={2}
        empty="no week has settled yet"
        footer={
          <p className="integrity-note">
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
            <td className={r.ratio !== null && r.ratio > 1 ? "integrity-warn" : ""}>
              {r.ratio === null ? "—" : fmtPct(r.ratio * 100, 0)}
            </td>
          </tr>
        ))}
      </DataCard>
    </div>
  );
}
