import { useEffect, useRef, useState, type ReactNode } from "react";
import { useSearchParams } from "react-router-dom";
import {
  createChart,
  createSeriesMarkers,
  CandlestickSeries,
  LineSeries,
  LineStyle,
  type IChartApi,
  type SeriesMarker,
  type Time,
  type UTCTimestamp,
} from "lightweight-charts";
import type { TechnicalsChart, TechnicalsVendorLevel } from "@console/shared";
import { useTechnicalsChart } from "../../lib/api";
import { SERIES_COLORS } from "../../components/Charts";
import { fmtIvr } from "../../lib/format";

/**
 * One name's chart: our bars, our level grid, CCI and the trend scores, with the vendor's levels
 * and trend grades beside them where the vendor's chart was captured.
 *
 * Everything drawn is `packages/technicals`' chart file (`data/technicals/charts/<SYMBOL>.json`);
 * the page computes nothing. The comparison is the point: level SELECTION is unsolved, so the page
 * draws our grid's extremes and the vendor's levels, and marks each vendor level with whether we can
 * produce it -- support and resistance on our grid, gap levels as an edge of one of our own gaps. A
 * disagreement you can see, not a claim that we draw the vendor's levels.
 *
 * Styling carries that distinction: a support or resistance we place is a solid line, a gap level we
 * place is dotted, and any level we cannot produce is dashed.
 */

const UP = "#43b57a";
const DOWN = "#d95c4a";
const GRID_INK = "#8a93a3";
const OURS = SERIES_COLORS[0]!;
const VENDOR = SERIES_COLORS[2]!;

/** The scan rules that read bullish; the other three read bearish. */
const BULLISH = new Set(["BullishCounterTrend", "CciDipInBullishTrend", "BullishTrendFollowing"]);

const RULE_LABEL: Record<string, string> = {
  BullishTrendFollowing: "Bullish trend following",
  BearishTrendFollowing: "Bearish trend following",
  BullishCounterTrend: "Bullish counter-trend",
  BearishCounterTrend: "Bearish counter-trend",
  CciDipInBullishTrend: "CCI dip in a bullish trend",
  CciRallyInBearishTrend: "CCI rally in a bearish trend",
};

const KIND_LABEL: Record<string, string> = {
  support: "support",
  resistance: "resistance",
  gapSupport: "gap support",
  gapResistance: "gap resistance",
};

function t(date: string): UTCTimestamp {
  return (Date.parse(`${date}T00:00:00Z`) / 1000) as UTCTimestamp;
}

function fmt(v: number | null | undefined, digits = 2): string {
  if (v === null || v === undefined) return "—";
  return v.toLocaleString(undefined, { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

function levelColor(l: TechnicalsVendorLevel): string {
  return l.kind === "support" || l.kind === "gapSupport" ? UP : DOWN;
}

const GAP_KINDS = new Set(["gapSupport", "gapResistance"]);

function levelStyle(l: TechnicalsVendorLevel): LineStyle {
  if (l.onOurGrid === false) return LineStyle.Dashed;
  return GAP_KINDS.has(l.kind) ? LineStyle.Dotted : LineStyle.Solid;
}

/** "Placed / asked" over one family of levels; null placements (a capture with no grid) are left out. */
function placement(levels: TechnicalsVendorLevel[]): { placed: number; asked: number } {
  const asked = levels.filter((l) => l.onOurGrid !== null);
  return { placed: asked.filter((l) => l.onOurGrid === true).length, asked: asked.length };
}

/** A line series from an aligned array; a null is whitespace (a gap), never a zero. */
function line(dates: string[], values: (number | null)[]) {
  return dates.map((d, i) => {
    const v = values[i];
    return v === null || v === undefined ? { time: t(d) } : { time: t(d), value: v };
  });
}

function PriceChart({ c }: { c: TechnicalsChart }) {
  const hostRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const el = hostRef.current;
    if (el === null || c.bars.length < 2) return;
    const chart: IChartApi = createChart(el, {
      autoSize: true,
      layout: { background: { color: "transparent" }, textColor: "#a6adb8", panes: { separatorColor: "#23262d" } },
      grid: { vertLines: { color: "#1a1d23" }, horzLines: { color: "#1a1d23" } },
      rightPriceScale: { borderColor: "#23262d" },
      timeScale: { borderColor: "#23262d" },
    });
    const dates = c.bars.map((b) => b.date);

    // Pane 0: candles, our grid's extremes, the vendor's levels, our scan-rule matches.
    const candles = chart.addSeries(CandlestickSeries, {
      upColor: UP,
      downColor: DOWN,
      borderVisible: false,
      wickUpColor: UP,
      wickDownColor: DOWN,
      priceLineVisible: false,
    });
    candles.setData(c.bars.map((b) => ({ time: t(b.date), open: b.open, high: b.high, low: b.low, close: b.close })));
    if (c.grid) {
      for (const [price, title] of [
        [c.grid.high, "grid high"],
        [c.grid.low, "grid low"],
      ] as const) {
        candles.createPriceLine({ price, color: GRID_INK, lineWidth: 1, lineStyle: LineStyle.LargeDashed, axisLabelVisible: true, title });
      }
    }
    for (const l of c.vendor?.levels ?? []) {
      candles.createPriceLine({
        price: l.value,
        color: levelColor(l),
        lineWidth: 1,
        lineStyle: levelStyle(l),
        axisLabelVisible: true,
        title: `vendor ${KIND_LABEL[l.kind] ?? l.kind}`,
      });
    }
    const inRange = new Set(dates);
    const markers: SeriesMarker<Time>[] = c.signals
      .filter((s) => inRange.has(s.date))
      .map((s) => {
        const bull = s.rules.some((r) => BULLISH.has(r));
        return {
          time: t(s.date),
          position: bull ? "belowBar" : "aboveBar",
          color: bull ? UP : DOWN,
          shape: bull ? "arrowUp" : "arrowDown",
          text: "",
        };
      });
    createSeriesMarkers(candles, markers);

    // Pane 1: CCI 14 (the scan rules' trend-following input) and CCI 5 (their dip/rally input).
    const cci14 = chart.addSeries(LineSeries, { color: OURS, lineWidth: 2, title: "CCI 14", priceLineVisible: false }, 1);
    cci14.setData(line(dates, c.cci14));
    const cci5 = chart.addSeries(LineSeries, { color: SERIES_COLORS[3]!, lineWidth: 1, title: "CCI 5", priceLineVisible: false }, 1);
    cci5.setData(line(dates, c.cci5));
    for (const price of [100, -100]) {
      cci14.createPriceLine({ price, color: GRID_INK, lineWidth: 1, lineStyle: LineStyle.Dotted, axisLabelVisible: false, title: "" });
    }

    // Pane 2: the short-term trend score, ours against the vendor's grade for the same day.
    const ours = chart.addSeries(LineSeries, { color: OURS, lineWidth: 2, lineType: 1, title: "trend (ours)", priceLineVisible: false }, 2);
    ours.setData(line(dates, c.trendShort));
    if (c.vendor && c.vendor.trendShort.length > 0) {
      const first = dates[0]!;
      const theirs = chart.addSeries(LineSeries, { color: VENDOR, lineWidth: 2, lineType: 1, lineStyle: LineStyle.Dashed, title: "trend (vendor)", priceLineVisible: false }, 2);
      theirs.setData(c.vendor.trendShort.filter((p) => p.date >= first).map((p) => ({ time: t(p.date), value: p.value })));
    }

    // Relative, not pixel, heights: a pixel height set before the first layout is overridden.
    const panes = chart.panes();
    panes[0]?.setStretchFactor(0.6);
    panes[1]?.setStretchFactor(0.22);
    panes[2]?.setStretchFactor(0.18);
    chart.timeScale().fitContent();
    return () => chart.remove();
  }, [c]);

  if (c.bars.length < 2) return <p className="muted">Not enough bars to draw.</p>;
  return <div ref={hostRef} style={{ height: "620px" }} />;
}

function Card({ title, children, asOf }: { title: string; children: ReactNode; asOf?: string }) {
  return (
    <section className="card">
      <div className="card-head">
        <h2>{title}</h2>
        <span className="chip">record-only</span>
        {asOf !== undefined && <span className="card-asof">{asOf}</span>}
      </div>
      {children}
    </section>
  );
}

function VendorCard({ c }: { c: TechnicalsChart }) {
  const v = c.vendor;
  if (!v) {
    return (
      <Card title="Against the vendor">
        <p className="muted">The vendor's chart for {c.symbol} has not been captured; only ours is drawn.</p>
      </Card>
    );
  }
  const onGrid = placement(v.levels.filter((l) => !GAP_KINDS.has(l.kind)));
  const onGaps = placement(v.levels.filter((l) => GAP_KINDS.has(l.kind)));
  const agree = v.barsCompared ? v.barsAgree ?? 0 : null;
  return (
    <Card title="Against the vendor" asOf={`captured ${v.capture}, bars through ${v.through ?? "—"}`}>
      <div className="stats-grid">
        <div className="stat-tile">
          <span className="stat-label">levels our grid places</span>
          <span className="stat-value">
            {onGrid.placed} of {onGrid.asked}
          </span>
          <span className="stat-label muted">support and resistance</span>
        </div>
        <div className="stat-tile">
          <span className="stat-label">gap levels we place</span>
          <span className="stat-value">{onGaps.asked === 0 ? "—" : `${onGaps.placed} of ${onGaps.asked}`}</span>
          <span className="stat-label muted">an edge of one of our gaps, on its bar</span>
        </div>
        <div className="stat-tile">
          <span className="stat-label">bars agreeing to the cent</span>
          <span className="stat-value">
            {agree === null ? "—" : `${agree} of ${v.barsCompared}`}
          </span>
          <span className="stat-label muted">prices; a grid is only as exact as its bars</span>
        </div>
        <div className="stat-tile">
          <span className="stat-label">vendor rank</span>
          <span className="stat-value">{v.rank ?? "—"}</span>
          <span className="stat-label muted">1–10</span>
        </div>
        <div className="stat-tile">
          <span className="stat-label">vendor IV rank</span>
          <span className="stat-value">{v.ivRank === null ? "—" : fmtIvr(v.ivRank / 100)}</span>
          <span className="stat-label muted">
            ours {c.ivRank ? fmtIvr(c.ivRank.value / 100) : "—"}; a different IV series
          </span>
        </div>
      </div>
      {v.levels.length === 0 ? (
        <p className="muted">The capture carries no levels.</p>
      ) : (
        <table className="data-table">
          <thead>
            <tr>
              <th>Level</th>
              <th>Price</th>
              <th>Dated</th>
              <th title="Support and resistance: a point on our grid. Gap levels: an edge of one of our own gaps, on the same bar.">
                We place it
              </th>
            </tr>
          </thead>
          <tbody>
            {[...v.levels]
              .sort((a, b) => b.value - a.value)
              .map((l) => (
                <tr key={`${l.kind}-${l.value}`}>
                  <td style={{ color: levelColor(l) }}>{KIND_LABEL[l.kind] ?? l.kind}</td>
                  <td>{fmt(l.value)}</td>
                  <td className="muted">{l.date ?? "—"}</td>
                  <td className={l.onOurGrid === false ? "pnl-neg" : l.onOurGrid === null ? "muted" : ""}>
                    {l.onOurGrid === null ? "—" : l.onOurGrid ? (GAP_KINDS.has(l.kind) ? "gap edge" : "on grid") : "no"}
                  </td>
                </tr>
              ))}
          </tbody>
        </table>
      )}
      <p className="muted">
        On the chart: solid, a support or resistance our grid places; dotted, a gap level that is an edge of one
        of our gaps; dashed, a level we cannot produce. Which of those candidates the vendor chooses to draw is
        not yet reproduced.
      </p>
    </Card>
  );
}

function SignalsCard({ c }: { c: TechnicalsChart }) {
  const recent = [...c.signals].reverse().slice(0, 15);
  return (
    <Card title="Scan-rule matches" asOf={`${c.signals.length} in ${c.bars.length} sessions`}>
      {recent.length === 0 ? (
        <p className="muted">No rule matched in the sessions drawn.</p>
      ) : (
        <table className="data-table">
          <thead>
            <tr>
              <th>Session</th>
              <th>Rules</th>
            </tr>
          </thead>
          <tbody>
            {recent.map((s) => (
              <tr key={s.date}>
                <td>{s.date}</td>
                <td>{s.rules.map((r) => RULE_LABEL[r] ?? r).join(", ")}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <p className="muted">Arrows on the chart: up for the bullish rules, down for the bearish. A pattern, not a trade idea.</p>
    </Card>
  );
}

export function ChartPage() {
  const [params, setParams] = useSearchParams();
  const symbol = params.get("symbol")?.toUpperCase() || undefined;
  const { data, isLoading, isError } = useTechnicalsChart(symbol);
  const c = data?.chart ?? null;
  const index = data?.index;
  const known = new Set((index?.symbols ?? []).map((s) => s.symbol));
  // The box is for picking the next name, so it empties on every navigation; the name being
  // shown is the heading above the chart.
  const [draft, setDraft] = useState("");

  const go = (s: string) => {
    const next = new URLSearchParams(params);
    if (s) next.set("symbol", s.toUpperCase());
    else next.delete("symbol");
    setParams(next);
    setDraft("");
  };
  const last = c ? c.bars[c.bars.length - 1] : undefined;

  return (
    <div className="page">
      <div className="page-title-row">
        <h1>Chart</h1>
        <form
          onSubmit={(e) => {
            e.preventDefault();
            if (draft.trim()) go(draft.trim());
          }}
        >
          <input
            className="chip review-session-select"
            list="technicals-symbols"
            value={draft}
            placeholder="symbol"
            aria-label="Symbol"
            onChange={(e) => {
              const v = e.target.value;
              // A pick from the suggestions arrives as a replacement (or, in some browsers, as a
              // plain Event); a keystroke arrives as insertText. Only a pick navigates, or typing
              // "A" on the way to "AAPL" would open A's chart.
              const inputType = (e.nativeEvent as InputEvent).inputType;
              const picked = inputType === undefined || inputType === "insertReplacementText";
              if (picked && known.has(v.toUpperCase())) go(v);
              else setDraft(v);
            }}
          />
          <datalist id="technicals-symbols">
            {(index?.symbols ?? []).map((s) => (
              <option key={s.symbol} value={s.symbol}>
                {s.vendor ? "vendor captured" : ""}
              </option>
            ))}
          </datalist>
        </form>
        {c && <span className="card-asof">close of {c.session}</span>}
      </div>

      {isError && <p className="muted">Could not read the technicals store.</p>}
      {isLoading && <p className="muted">Reading the chart…</p>}
      {!isLoading && !isError && symbol === undefined && (
        <p className="muted">
          Pick a name — {index?.symbols.length ?? 0} charted
          {index ? `, ${index.symbols.filter((s) => s.vendor).length} with the vendor's chart captured` : ""}.
        </p>
      )}
      {!isLoading && !isError && symbol !== undefined && c === null && (
        <p className="muted">No chart for {symbol}. The report job charts every stock the technicals store holds.</p>
      )}

      {c && (
        <>
          <div className="chart-symbol-row">
            <h2 className="chart-symbol">{c.symbol}</h2>
            {c.sentiment && (
              <span
                className={`chip ${c.sentiment === "Bullish" ? "pnl-pos" : c.sentiment === "Bearish" ? "pnl-neg" : ""}`}
                title="Close against the 50-session SMA and the 200-session WMA: above both Bullish, below both Bearish, between them Neutral -- the vendor's own rule, matched on every capture."
              >
                {c.sentiment}
              </span>
            )}
            {c.ivRank && (
              <span
                className="chip"
                title={`IV rank as of ${c.ivRank.date}, ${
                  c.ivRank.source === "dolt" ? "ranked from Dolt's IV history" : "tastytrade's (Dolt has no IV for this name)"
                }. Neither source is the vendor's IV, so the two can differ by several points.`}
              >
                IVR {fmtIvr(c.ivRank.value / 100)}
                {c.ivRank.source !== "dolt" && <span className="muted"> · tastytrade</span>}
              </span>
            )}
            {c.vendor?.sentiment && c.vendor.sentiment !== c.sentiment && (
              <span className="chip muted" title={`The vendor's label as captured ${c.vendor.capture}`}>
                vendor: {c.vendor.sentiment}
              </span>
            )}
          </div>
          <Card title="Price, CCI and trend" asOf={last ? `close ${fmt(last.close)}` : undefined}>
            <PriceChart c={c} />
            <p className="muted">
              {c.grid
                ? `Grid over ${c.grid.window ?? 250} sessions: low ${fmt(c.grid.low)} (${c.grid.lowDate ?? "—"}), high ${fmt(c.grid.high)} (${c.grid.highDate ?? "—"}), step ${fmt(c.grid.step)}. `
                : "Too few sessions for a grid. "}
              Middle pane: CCI 14 and CCI 5 with ±100. Bottom: the short-term trend score (−4 to +4), ours solid
              {c.vendor ? ", the vendor's dashed" : ""}.
            </p>
          </Card>
          <VendorCard c={c} />
          <SignalsCard c={c} />
        </>
      )}
    </div>
  );
}
