import type { PmccWeeklyRow } from "@console/shared";
import { usePmccWeekly } from "../../lib/api";
import { Card, PnlCell } from "../../components/DataTable";
import { TimeLineChart } from "../../components/chart/TimeLineChart";

/**
 * The arms, week by week -- the paired readout the held-long experiment is judged on.
 *
 * Every arm trades the same symbols on the same weeks, so the comparison is a column per (arm,
 * symbol) and a row per ISO week: the change in that arm's net P&L over the week, from the module's
 * own `analytics.weekly_by_arm`. A closed control cycle lands in the week it closed; a held-long
 * position moves every week it is open, marked to market. Read down a column for an arm's path,
 * across a row for one week's A/B. Symbols are never pooled (the module's rule: each is its own
 * population), so the symbol filter picks columns rather than summing them.
 */
export function WeeklyByArmCard({ symbol, era = null }: { symbol: string | null; era?: string | null }) {
  const { data, isLoading, dataUpdatedAt } = usePmccWeekly(era);
  const rows = (data?.data ?? []).filter((r) => symbol === null || r.symbol === symbol);
  return (
    <Card title="weekly by arm" collapseKey="pmcc-weekly" updatedAt={dataUpdatedAt} isError={data?.ok === false}>
      {isLoading ? (
        <p className="muted">loading…</p>
      ) : data?.ok === false ? (
        <p className="muted">{data.error}</p>
      ) : rows.length === 0 ? (
        <p className="muted">no week on file in this era yet</p>
      ) : (
        <WeeklyByArm rows={rows} />
      )}
    </Card>
  );
}

export function WeeklyByArm({ rows }: { rows: PmccWeeklyRow[] }) {
  const series = [...new Set(rows.map((r) => `${r.arm} · ${r.symbol}`))].sort();
  const weeks = [...new Set(rows.map((r) => r.week))].sort();
  const cell = new Map(rows.map((r) => [`${r.week}|${r.arm} · ${r.symbol}`, r]));
  const chart = series.map((label) => ({
    label,
    points: rows
      .filter((r) => `${r.arm} · ${r.symbol}` === label)
      .sort((a, b) => a.week.localeCompare(b.week))
      .map((r) => ({ x: r.weekEnd, y: r.cumulative })),
  }));
  return (
    <>
      <div className="table-scroll">
        <table className="data-table num-from-1">
          <thead>
            <tr>
              <th>week</th>
              {series.map((s) => (
                <th key={s} className="mono">
                  {s}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {weeks.map((w) => (
              <tr key={w}>
                <td>{w}</td>
                {series.map((s) => {
                  const r = cell.get(`${w}|${s}`);
                  return (
                    <td
                      key={s}
                      title={
                        r === undefined
                          ? "no position that week"
                          : `${r.positions} position(s) · cumulative ${r.cumulative.toFixed(2)}${r.unpriced ? ` · ${r.unpriced} unpriced` : ""}`
                      }
                    >
                      {r === undefined ? <span className="muted">—</span> : <PnlCell v={r.change} />}
                      {r !== undefined && r.unpriced > 0 && <span className="chip chip-warn integrity-chip">unpriced</span>}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {chart.some((c) => c.points.length >= 2) && <TimeLineChart series={chart} height={200} />}
      <p className="integrity-note">
        Each cell is the change in that arm&apos;s net P&amp;L over the week, after every cost; the chart runs it forward.
        A position unpriceable at a week&apos;s close is flagged and left out of that week, never guessed in.
      </p>
    </>
  );
}
