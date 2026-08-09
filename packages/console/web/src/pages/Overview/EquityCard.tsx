import { useEffect, useRef } from "react";
import { useQuery } from "@tanstack/react-query";
import { createChart, LineSeries, type IChartApi, type UTCTimestamp } from "lightweight-charts";
import { fmtMoney } from "../../components/DataTable";

interface SuiteReport {
  suite: { net: number; trades: number; wins: number; losses: number; winRatePct: number | null; avg: number | null };
  daily: Array<{ session: string; net: number; cumulative: number; byModule: Record<string, number> }>;
  modules: Record<string, { net: number; trades: number; wins: number; losses: number }>;
}

export function useSuiteReport() {
  return useQuery<SuiteReport>({
    queryKey: ["report"],
    queryFn: async () => {
      const res = await fetch("/api/report");
      if (!res.ok) throw new Error(`report: HTTP ${res.status}`);
      return (await res.json()) as SuiteReport;
    },
    refetchInterval: 60_000,
  });
}

const MODULE_COLORS: Record<string, string> = {
  meic: "#7aa2ff",
  flies: "#d9a13b",
  earnings: "#a06bd9",
};

/** Suite equity — cumulative net P&L (paper) with per-module lines, the suite dashboard's core card. */
export function EquityCard() {
  const { data } = useSuiteReport();
  const hostRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);

  useEffect(() => {
    const el = hostRef.current;
    if (el === null || data === undefined || data.daily.length === 0) return;
    const chart = createChart(el, {
      autoSize: true,
      layout: { background: { color: "transparent" }, textColor: "#a6adb8" },
      grid: { vertLines: { color: "#1a1d23" }, horzLines: { color: "#1a1d23" } },
      rightPriceScale: { borderColor: "#23262d" },
      timeScale: { borderColor: "#23262d" },
    });
    chartRef.current = chart;
    const t = (session: string) => (Date.parse(session + "T00:00:00Z") / 1000) as UTCTimestamp;

    const suite = chart.addSeries(LineSeries, { color: "#d23f57", lineWidth: 2, title: "suite" });
    suite.setData(data.daily.map((d) => ({ time: t(d.session), value: d.cumulative })));

    // Top modules by |total| get their own cumulative lines.
    const totals = Object.entries(data.modules).sort((a, b) => Math.abs(b[1].net) - Math.abs(a[1].net)).slice(0, 3);
    for (const [mod] of totals) {
      const running: Array<{ time: UTCTimestamp; value: number }> = [];
      let cum = 0;
      for (const d of data.daily) {
        cum += d.byModule[mod] ?? 0;
        running.push({ time: t(d.session), value: cum });
      }
      chart
        .addSeries(LineSeries, {
          color: MODULE_COLORS[mod] ?? "#82878f",
          lineWidth: 1,
          title: mod,
          priceLineVisible: false,
        })
        .setData(running);
    }
    chart.timeScale().fitContent();
    return () => {
      chart.remove();
      chartRef.current = null;
    };
  }, [data]);

  const s = data?.suite;
  return (
    <section className="card">
      <h2>suite equity — paper ({data?.daily.length ?? 0} sessions · cumulative net P&L)</h2>
      <div className="stats-grid" style={{ marginBottom: "0.7rem" }}>
        <div className="stat-tile">
          <span className="stat-label">net</span>
          <span className={`stat-value ${s !== undefined && s.net >= 0 ? "pnl-pos" : "pnl-neg"}`}>
            {s !== undefined ? fmtMoney(s.net) : "—"}
          </span>
        </div>
        <div className="stat-tile">
          <span className="stat-label">trades</span>
          <span className="stat-value">{s?.trades ?? "—"}</span>
        </div>
        <div className="stat-tile">
          <span className="stat-label">win rate</span>
          <span className="stat-value">
            {s?.winRatePct != null ? `${s.winRatePct.toFixed(1)}% (${s.wins}/${s.losses})` : "—"}
          </span>
        </div>
        <div className="stat-tile">
          <span className="stat-label">avg / trade</span>
          <span className="stat-value">{s?.avg != null ? fmtMoney(s.avg) : "—"}</span>
        </div>
      </div>
      <div ref={hostRef} style={{ height: "16rem" }}>
        {data === undefined && <span className="skeleton skeleton-text" style={{ width: "40%" }} />}
        {data !== undefined && data.daily.length === 0 && <p className="muted">no closed paper sessions yet</p>}
      </div>
    </section>
  );
}

interface LogLine {
  source: string;
  level: string;
  ts: string | null;
  text: string;
}

export function LogsCard() {
  const { data } = useQuery<{ lines: LogLine[] }>({
    queryKey: ["logs"],
    queryFn: async () => {
      const res = await fetch("/api/logs");
      if (!res.ok) throw new Error(`logs: HTTP ${res.status}`);
      return (await res.json()) as { lines: LogLine[] };
    },
    refetchInterval: 15_000,
  });
  const lines = data?.lines ?? [];
  return (
    <section className="card">
      <h2>recent logs (watchdog · notify · module paper logs)</h2>
      {data === undefined ? (
        <span className="skeleton skeleton-text" style={{ width: "60%" }} />
      ) : lines.length === 0 ? (
        <p className="muted">no log lines found</p>
      ) : (
        <div>
          {[...lines].reverse().map((l, i) => (
            <div key={i} className="log-line">
              <span className={`log-level lvl-${l.level}`}>{l.level}</span>
              <span className="log-source">{l.source}</span>
              <span className="log-text" title={l.text}>
                {l.ts !== null ? `${l.ts.slice(11, 19)} ` : ""}
                {l.text}
              </span>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}
