import { useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { createChart, LineSeries, type IChartApi, type ISeriesApi, type UTCTimestamp } from "lightweight-charts";
import { fmtMoney } from "../../components/DataTable";
import { useFlashOnChange } from "../../lib/useFlashOnChange";

interface SuiteReport {
  era: { from: string | null; note: string | null };
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
  const seriesRef = useRef<Map<string, ISeriesApi<"Line">>>(new Map());

  // Chart mount/teardown, once — separate from the data effect below so a 60s refetch (a new
  // object reference every time, even when values are unchanged) updates the existing series in
  // place via setData() rather than tearing down and rebuilding the whole chart, which previously
  // discarded any pan/zoom the viewer had set every single poll.
  useEffect(() => {
    const el = hostRef.current;
    if (el === null) return;
    const chart = createChart(el, {
      autoSize: true,
      layout: { background: { color: "transparent" }, textColor: "#a6adb8" },
      grid: { vertLines: { color: "#1a1d23" }, horzLines: { color: "#1a1d23" } },
      rightPriceScale: { borderColor: "#23262d" },
      timeScale: { borderColor: "#23262d" },
    });
    chartRef.current = chart;
    return () => {
      chart.remove();
      chartRef.current = null;
      seriesRef.current.clear();
    };
  }, []);

  useEffect(() => {
    const chart = chartRef.current;
    if (chart === null || data === undefined || data.daily.length === 0) return;
    const t = (session: string) => (Date.parse(session + "T00:00:00Z") / 1000) as UTCTimestamp;
    const series = seriesRef.current;
    const hadNoSeriesYet = series.size === 0;

    // No combined "suite" line: these books differ in scale by more than an order of magnitude
    // (see Review's own note), so a summed line would describe the largest one and imply it
    // described all three. Per-module lines only.
    const totals = Object.entries(data.modules).sort((a, b) => Math.abs(b[1].net) - Math.abs(a[1].net)).slice(0, 3);
    const wantedMods = new Set(totals.map(([mod]) => mod));

    // The top-3-by-|net| set can change membership between polls -- drop a line that fell out.
    for (const [mod, line] of series) {
      if (!wantedMods.has(mod)) {
        chart.removeSeries(line);
        series.delete(mod);
      }
    }

    for (const [mod] of totals) {
      const running: Array<{ time: UTCTimestamp; value: number }> = [];
      let cum = 0;
      for (const d of data.daily) {
        cum += d.byModule[mod] ?? 0;
        running.push({ time: t(d.session), value: cum });
      }
      let line = series.get(mod);
      if (line === undefined) {
        line = chart.addSeries(LineSeries, {
          color: MODULE_COLORS[mod] ?? "#82878f",
          lineWidth: 1,
          title: mod,
          priceLineVisible: false,
        });
        series.set(mod, line);
      }
      line.setData(running);
    }
    // Only fit the view the first time data arrives -- on every later poll, an already-open chart
    // keeps whatever pan/zoom the viewer set rather than snapping back to fitContent().
    if (hadNoSeriesYet) chart.timeScale().fitContent();
  }, [data]);

  const s = data?.suite;
  const tradesFlash = useFlashOnChange<HTMLSpanElement>(s?.trades);
  const avgFlash = useFlashOnChange<HTMLSpanElement>(s?.avg);
  return (
    <section className="card">
      {/* Era-bounded since 2026-08-21: the suite report starts at data_epoch, so this curve
          covers the declared era rather than all of history — pooling across the advisor-era
          boundary would draw one line through two incomparable experiments. */}
      <h2>
        suite equity — paper ({data?.daily.length ?? 0} sessions
        {data?.era.from != null && <> · era from {data.era.from}</>} · cumulative net P&L)
      </h2>
      <div className="stats-grid" style={{ marginBottom: "0.7rem" }}>
        {/* No combined "suite net" tile, deliberately -- see the chart's own note: these books
            differ in scale by more than an order of magnitude, so a summed dollar figure would
            describe the largest one (MEIC) and imply it described all three. Per-module net is
            one line-hover away on the chart below; count/rate aggregates here are honest sums,
            not scale-dominated the same way a dollar total is. */}
        <div className="stat-tile">
          <span className="stat-label">trades</span>
          <span ref={tradesFlash} className="stat-value">{s?.trades ?? "—"}</span>
        </div>
        <div className="stat-tile">
          <span className="stat-label">win rate</span>
          <span className="stat-value">
            {s?.winRatePct != null ? `${s.winRatePct.toFixed(1)}% (${s.wins}/${s.losses})` : "—"}
          </span>
        </div>
        <div className="stat-tile">
          <span className="stat-label">avg / trade</span>
          <span ref={avgFlash} className="stat-value">{s?.avg != null ? fmtMoney(s.avg) : "—"}</span>
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

const LOG_LEVELS = ["ALL", "CRITICAL", "WARN", "INFO", "NOTIFY", "OK"] as const;

export function LogsCard() {
  const [level, setLevel] = useState<(typeof LOG_LEVELS)[number]>("ALL");
  const { data } = useQuery<{ lines: LogLine[] }>({
    queryKey: ["logs"],
    queryFn: async () => {
      const res = await fetch("/api/logs");
      if (!res.ok) throw new Error(`logs: HTTP ${res.status}`);
      return (await res.json()) as { lines: LogLine[] };
    },
    refetchInterval: 15_000,
  });
  const all = data?.lines ?? [];
  const lines = all.filter((l) => {
    if (level === "ALL") return true;
    if (level === "CRITICAL") return l.level === "CRITICAL" || l.level === "ERROR";
    if (level === "WARN") return l.level === "WARN" || l.level === "WARNING";
    return l.level === level;
  });
  return (
    <section className="card">
      <div className="card-head">
        <h2>recent logs (watchdog · notify · module paper logs)</h2>
        <div className="mode-toggle" style={{ marginLeft: "auto" }} role="group" aria-label="log level filter">
          {LOG_LEVELS.map((lv) => (
            <button key={lv} type="button" className={level === lv ? "mode-btn active" : "mode-btn"} onClick={() => setLevel(lv)}>
              {lv}
            </button>
          ))}
        </div>
      </div>
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
