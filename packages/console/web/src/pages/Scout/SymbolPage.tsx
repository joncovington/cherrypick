import { useEffect, useRef } from "react";
import { useParams, Link } from "react-router-dom";
import {
  createChart,
  CandlestickSeries,
  LineSeries,
  type IChartApi,
  type ISeriesApi,
  type IPriceLine,
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
  const candlesRef = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const overlaysRef = useRef<Map<string, ISeriesApi<"Line">>>(new Map());
  const priceLinesRef = useRef<IPriceLine[]>([]);

  // Chart + candlestick series mount/teardown, once — separate from the data effect below so a
  // refetch updates the existing series in place (setData / price-line refresh) instead of tearing
  // down and rebuilding the whole chart, which previously discarded the viewer's pan/zoom on every
  // poll.
  useEffect(() => {
    const el = containerRef.current;
    if (el === null) return;
    const chart = createChart(el, {
      autoSize: true,
      layout: { background: { color: "transparent" }, textColor: "#a6adb8" },
      grid: { vertLines: { color: "#1a1d23" }, horzLines: { color: "#1a1d23" } },
      rightPriceScale: { borderColor: "#23262d" },
      timeScale: { borderColor: "#23262d" },
    });
    chartRef.current = chart;
    candlesRef.current = chart.addSeries(CandlestickSeries, {
      upColor: "#43b57a",
      downColor: "#d95c4a",
      borderVisible: false,
      wickUpColor: "#43b57a",
      wickDownColor: "#d95c4a",
    });
    return () => {
      chart.remove();
      chartRef.current = null;
      candlesRef.current = null;
      overlaysRef.current.clear();
      priceLinesRef.current = [];
    };
  }, []);

  useEffect(() => {
    const chart = chartRef.current;
    const candles = candlesRef.current;
    if (chart === null || candles === null || data === undefined) return;
    const hadNoBarsYet = candles.data().length === 0;

    candles.setData(
      data.bars.map((b) => ({
        time: b.t as UTCTimestamp,
        open: b.o,
        high: b.h,
        low: b.l,
        close: b.c,
      })),
    );

    // SMA overlays are a fixed name set in practice (SMA_COLORS), but diffed by name anyway rather
    // than assumed, the same way EquityCard's per-module lines are.
    const overlays = overlaysRef.current;
    const wanted = new Set(Object.keys(data.overlays));
    for (const [name, series] of overlays) {
      if (!wanted.has(name)) {
        chart.removeSeries(series);
        overlays.delete(name);
      }
    }
    for (const [name, values] of Object.entries(data.overlays)) {
      let series = overlays.get(name);
      if (series === undefined) {
        series = chart.addSeries(LineSeries, {
          color: SMA_COLORS[name] ?? "#82878f",
          lineWidth: 1,
          priceLineVisible: false,
          lastValueVisible: false,
        });
        overlays.set(name, series);
      }
      series.setData(
        values
          .map((v, i) => (v === null ? null : { time: data.bars[i]!.t as UTCTimestamp, value: v }))
          .filter((p): p is { time: UTCTimestamp; value: number } => p !== null),
      );
    }

    // Support/resistance as horizontal price lines on the candle series. No setData equivalent for
    // price lines, but they're cheap -- clear and re-add rather than diff by level identity.
    for (const line of priceLinesRef.current) candles.removePriceLine(line);
    priceLinesRef.current = data.levels
      .filter((l) => l.touches >= 2)
      .map((level) =>
        candles.createPriceLine({
          price: level.price,
          color: level.kind === "support" ? "#43b57a66" : "#d95c4a66",
          lineWidth: 1,
          lineStyle: 2,
          axisLabelVisible: false,
          title: `${level.kind} ×${level.touches}`,
        }),
      );

    // Only fit the view the first time bars arrive -- on every later refetch, keep whatever
    // pan/zoom the viewer set rather than snapping back to fitContent().
    if (hadNoBarsYet) chart.timeScale().fitContent();
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
        <Link
          to={`/scout/builder?symbol=${encodeURIComponent(sym)}`}
          className="link"
          style={{ marginLeft: "auto" }}
        >
          open in builder →
        </Link>
        <Link to="/scout" className="link">
          ← watchlist
        </Link>
      </div>

      <div className="cards cards-wide">
        <SymbolCard symbol={sym} />

        <section className="card">
          <h2>Daily — SMA 20/50/200, support/resistance</h2>
          {isError ? (
            <p className="muted">No price history yet for {sym} — try again shortly.</p>
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
