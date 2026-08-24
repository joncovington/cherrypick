import { fmtMoney } from "./DataTable";

/**
 * Daily net P&L calendar: one cell per session, alpha-scaled green/red, ISO-week columns with
 * Monday at the top. Extracted from the MEIC page (2026-08-23, docs/metrics-plan.md step 0) so
 * every module's history tab can carry the same consistency-at-a-glance view — the journal-tool
 * survey's single most-loved widget. Missing weekdays render as neutral cells (a holiday is not
 * a zero day); the window is the most recent 90 sessions.
 */
export interface CalendarDay {
  date: string;
  net: number;
  /** Count shown in the tooltip (trades, positions, weeks — whatever the module's unit is). */
  count: number;
}

export function CalendarHeatmap({ days, countLabel = "trades" }: { days: CalendarDay[]; countLabel?: string }) {
  const recent = days.slice(-90);
  if (recent.length === 0) return <p className="muted">no sessions</p>;
  const maxAbs = Math.max(...recent.map((d) => Math.abs(d.net)), 1);
  const weeks = new Map<string, Array<CalendarDay & { weekday: number }>>();
  for (const d of recent) {
    const dt = new Date(d.date + "T00:00:00Z");
    const weekday = (dt.getUTCDay() + 6) % 7;
    const monday = new Date(dt);
    monday.setUTCDate(dt.getUTCDate() - weekday);
    const key = monday.toISOString().slice(0, 10);
    let col = weeks.get(key);
    if (col === undefined) {
      col = [];
      weeks.set(key, col);
    }
    col.push({ ...d, weekday });
  }
  return (
    <div style={{ display: "flex", gap: 3, overflowX: "auto", paddingBottom: 4 }}>
      {[...weeks.entries()].map(([week, cells]) => (
        <div key={week} style={{ display: "grid", gridTemplateRows: "repeat(5, 14px)", gap: 3 }}>
          {[0, 1, 2, 3, 4].map((wd) => {
            const cell = cells.find((c) => c.weekday === wd);
            if (cell === undefined)
              return <div key={wd} style={{ width: 14, height: 14, background: "var(--row-line)", borderRadius: 2 }} />;
            const alpha = 0.15 + 0.85 * (Math.abs(cell.net) / maxAbs);
            const color = cell.net >= 0 ? `rgba(67, 181, 122, ${alpha})` : `rgba(217, 92, 74, ${alpha})`;
            return (
              <div
                key={wd}
                title={`${cell.date}: ${fmtMoney(cell.net)} (${cell.count} ${countLabel})`}
                style={{ width: 14, height: 14, background: color, borderRadius: 2 }}
              />
            );
          })}
        </div>
      ))}
    </div>
  );
}
