import { useEffect, useRef } from "react";
import { useParams, Link } from "react-router-dom";
import {
  createChart,
  CandlestickSeries,
  LineSeries,
  type IChartApi,
  type UTCTimestamp,
} from "lightweight-charts";
import { useSymbolAnalysis } from "../../lib/api";
import { AnalysisCard } from "./AnalysisCard";
import { useQuote } from "../../lib/useQuote";
import { SymbolCard } from "../../components/SymbolCard";

const SMA_COLORS: Record<string, string> = {
  sma20: "#d9a13b",
  sma50: "#7aa2ff",
  sma200: "#a06bd9",
};

function TrendChip({ label, grade }: { label: string; grade: string | null }) {
  const cls =
    grade === null
      ? ""
      : grade.includes("bullish")
        ? "chip-ok"
        : grade.includes("bearish")
          ? "chip-missing"
          : "chip-warn";
  return (
    <span className={`chip ${cls}`}>
      {label} {grade?.replace("_", " ") ?? "—"}
    </span>
  );
}

export function SymbolPage() {
  const { symbol = "" } = useParams();
  const sym = symbol.toUpperCase();
  const { data, isLoading, isError } = useSymbolAnalysis(sym);
  const quote = useQuote(sym);
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);

  useEffect(() => {
    const el = containerRef.current;
    if (el === null || data === undefined) return;
    const chart = createChart(el, {
      autoSize: true,
      layout: { background: { color: "transparent" }, textColor: "#a6adb8" },
      grid: { vertLines: { color: "#1a1d23" }, horzLines: { color: "#1a1d23" } },
      rightPriceScale: { borderColor: "#23262d" },
      timeScale: { borderColor: "#23262d" },
    });
    chartRef.current = chart;

    const candles = chart.addSeries(CandlestickSeries, {
      upColor: "#43b57a",
      downColor: "#d95c4a",
      borderVisible: false,
      wickUpColor: "#43b57a",
      wickDownColor: "#d95c4a",
    });
    candles.setData(
      data.bars.map((b) => ({
        time: b.t as UTCTimestamp,
        open: b.o,
        high: b.h,
        low: b.l,
        close: b.c,
      })),
    );

    for (const [name, values] of Object.entries(data.overlays)) {
      const series = chart.addSeries(LineSeries, {
        color: SMA_COLORS[name] ?? "#82878f",
        lineWidth: 1,
        priceLineVisible: false,
        lastValueVisible: false,
      });
      series.setData(
        values
          .map((v, i) => (v === null ? null : { time: data.bars[i]!.t as UTCTimestamp, value: v }))
          .filter((p): p is { time: UTCTimestamp; value: number } => p !== null),
      );
    }

    // Support/resistance as horizontal price lines on the candle series.
    for (const level of data.levels.filter((l) => l.touches >= 2)) {
      candles.createPriceLine({
        price: level.price,
        color: level.kind === "support" ? "#43b57a66" : "#d95c4a66",
        lineWidth: 1,
        lineStyle: 2,
        axisLabelVisible: false,
        title: `${level.kind} ×${level.touches}`,
      });
    }

    chart.timeScale().fitContent();
    return () => {
      chart.remove();
      chartRef.current = null;
    };
  }, [data]);

  const mid =
    quote?.last ??
    (quote?.bid !== undefined && quote?.ask !== undefined ? (quote.bid + quote.ask) / 2 : undefined);

  return (
    <div className="page">
      <div className="page-title-row">
        <h1>{sym}</h1>
        {mid !== undefined && (
          <span className={`chip ${quote?.source === "dxlink" ? "chip-live" : ""}`}>
            {mid.toFixed(2)} {quote?.source === "dxlink" ? "live" : "cached"}
          </span>
        )}
        {data && (
          <>
            <TrendChip label="1M" grade={data.trend["1m"]} />
            <TrendChip label="6M" grade={data.trend["6m"]} />
          </>
        )}
        <Link to="/scout" className="link" style={{ marginLeft: "auto" }}>
          ← watchlist
        </Link>
      </div>

      <div className="cards cards-wide">
        <SymbolCard symbol={sym} />

        <section className="card">
          <h2>Daily — SMA 20/50/200, support/resistance</h2>
          {isError ? (
            <p className="muted">
              No cached candles for {sym}. Candles come from scout's cache today — open the symbol in
              scout once to backfill, or wait for M5's own candle feed.
            </p>
          ) : (
            <div ref={containerRef} className="chart-host">
              {isLoading && <span className="skeleton skeleton-text" style={{ width: "40%" }} />}
            </div>
          )}
        </section>

        <AnalysisCard symbol={sym} />

        <section className="card">
          <h2>Levels</h2>
          <table className="data-table">
            <thead>
              <tr>
                <th>price</th>
                <th>kind</th>
                <th>touches</th>
              </tr>
            </thead>
            <tbody>
              {data?.levels
                .filter((l) => l.touches >= 2)
                .map((l) => (
                  <tr key={`${l.kind}-${l.price}`}>
                    <td>{l.price.toFixed(2)}</td>
                    <td className={l.kind === "support" ? "pnl-pos" : "pnl-neg"}>{l.kind}</td>
                    <td className="muted">{l.touches}</td>
                  </tr>
                ))}
            </tbody>
          </table>
        </section>
      </div>
    </div>
  );
}
