import { useQuery } from "@tanstack/react-query";
import type { TradingMode } from "@console/shared";
import { DataCard, PnlCell, fmtMoney } from "../../components/DataTable";

interface BreakdownRow {
  bucket: string;
  trades: number;
  sessions: number;
  winPct: number | null;
  avgNet: number | null;
}

interface DeepAnalytics {
  calendar: Array<{ date: string; net: number; trades: number }>;
  nlv: Array<{ date: string; nlv: number }>;
  byDelta: BreakdownRow[];
  byWing: BreakdownRow[];
  bySymbol: BreakdownRow[];
  byWeekday: BreakdownRow[];
  byHour: BreakdownRow[];
}

function useDeep(mode: TradingMode, symbol: string | null, profile: string | null) {
  const params = new URLSearchParams({ mode });
  if (symbol !== null) params.set("symbol", symbol);
  if (profile !== null) params.set("profile", profile);
  return useQuery<DeepAnalytics>({
    queryKey: ["meic-deep", mode, symbol, profile],
    queryFn: async () => {
      const res = await fetch(`/api/meic/deep?${params.toString()}`);
      if (!res.ok) throw new Error(`meic deep: HTTP ${res.status}`);
      return (await res.json()) as DeepAnalytics;
    },
    refetchInterval: 60_000,
  });
}

/** Daily net P&L calendar: one cell per session, alpha-scaled green/red, week columns. */
function Calendar({ days }: { days: Array<{ date: string; net: number; trades: number }> }) {
  const recent = days.slice(-90);
  if (recent.length === 0) return <p className="muted">no sessions</p>;
  const maxAbs = Math.max(...recent.map((d) => Math.abs(d.net)), 1);
  // Group into ISO week columns, Monday at the top.
  const weeks = new Map<string, Array<{ date: string; net: number; trades: number; weekday: number }>>();
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
            if (cell === undefined) return <div key={wd} style={{ width: 14, height: 14, background: "var(--row-line)", borderRadius: 2 }} />;
            const alpha = 0.15 + 0.85 * (Math.abs(cell.net) / maxAbs);
            const color = cell.net >= 0 ? `rgba(67, 181, 122, ${alpha})` : `rgba(217, 92, 74, ${alpha})`;
            return (
              <div
                key={wd}
                title={`${cell.date}: ${fmtMoney(cell.net)} (${cell.trades} trades)`}
                style={{ width: 14, height: 14, background: color, borderRadius: 2 }}
              />
            );
          })}
        </div>
      ))}
    </div>
  );
}

function NlvChart({ points }: { points: Array<{ date: string; nlv: number }> }) {
  if (points.length < 2) return <p className="muted">no EOD summaries yet</p>;
  const width = 720;
  const height = 160;
  const m = { l: 8, r: 8, t: 8, b: 8 };
  const ys = points.map((p) => p.nlv);
  const y0 = Math.min(...ys);
  const y1 = Math.max(...ys);
  const rising = ys[ys.length - 1]! >= ys[0]!;
  const sx = (i: number) => m.l + (i / (points.length - 1)) * (width - m.l - m.r);
  const sy = (v: number) => m.t + ((y1 - v) / Math.max(y1 - y0, 1)) * (height - m.t - m.b);
  return (
    <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label="NLV over time" style={{ width: "100%", height: "auto", display: "block" }}>
      <polyline
        points={points.map((p, i) => `${sx(i).toFixed(1)},${sy(p.nlv).toFixed(1)}`).join(" ")}
        fill="none"
        stroke={rising ? "#43b57a" : "#d95c4a"}
        strokeWidth={1.6}
      />
    </svg>
  );
}

function BreakdownCard({ title, rows, loading }: { title: string; rows: BreakdownRow[] | undefined; loading: boolean }) {
  return (
    <DataCard title={title} headers={["bucket", "trades", "sessions", "win %", "avg net"]} numFrom={1} loading={loading} rowCount={rows?.length ?? 0}>
      {rows?.map((r) => (
        <tr key={r.bucket}>
          <td>{r.bucket}</td>
          <td>{r.trades}</td>
          <td className="muted">{r.sessions}</td>
          <td>{r.winPct !== null ? `${r.winPct.toFixed(0)}%` : "—"}</td>
          <td>{r.avgNet !== null ? <PnlCell v={r.avgNet} /> : "—"}</td>
        </tr>
      ))}
    </DataCard>
  );
}

export function MeicDeepCards({
  mode,
  symbol = null,
  profile = null,
}: {
  mode: TradingMode;
  symbol?: string | null;
  profile?: string | null;
}) {
  const { data, isLoading } = useDeep(mode, symbol, profile);
  return (
    <>
      <section className="card">
        <h2>Daily net P&L (after fees) — calendar</h2>
        {isLoading ? <span className="skeleton skeleton-text" style={{ width: "40%" }} /> : <Calendar days={data?.calendar ?? []} />}
      </section>

      <section className="card">
        <h2>Account value (NLV) over time</h2>
        {isLoading ? <span className="skeleton skeleton-text" style={{ width: "40%" }} /> : <NlvChart points={data?.nlv ?? []} />}
      </section>

      <div className="cards" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(23rem, 1fr))" }}>
        <BreakdownCard title="By short-call delta" rows={data?.byDelta} loading={isLoading} />
        <BreakdownCard title="By wing width" rows={data?.byWing} loading={isLoading} />
        <BreakdownCard title="By symbol" rows={data?.bySymbol} loading={isLoading} />
        <BreakdownCard title="By weekday" rows={data?.byWeekday} loading={isLoading} />
        <BreakdownCard title="By entry hour (ET)" rows={data?.byHour} loading={isLoading} />
      </div>
    </>
  );
}
