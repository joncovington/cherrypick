import { useEffect, useRef, useState, type ReactNode } from "react";
import { useSearchParams } from "react-router-dom";
import {
  createChart,
  createSeriesMarkers,
  CandlestickSeries,
  LineSeries,
  LineStyle,
  type IChartApi,
  type IPrimitivePaneRenderer,
  type IPrimitivePaneView,
  type ISeriesApi,
  type ISeriesPrimitive,
  type SeriesAttachedParameter,
  type SeriesMarker,
  type SeriesType,
  type Time,
  type UTCTimestamp,
} from "lightweight-charts";
import type { TechnicalsChart, TechnicalsSetup, TechnicalsVendorLevel } from "@console/shared";
import { useTechnicalsChart } from "../../lib/api";
import { SERIES_COLORS } from "../../components/Charts";
import { fmtIvr } from "../../lib/format";

/**
 * One name's chart: our bars, our level grid, RSI and the trend scores, with the vendor's levels
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
 *
 * The arrows are one entry/exit setup at a time (`packages/technicals/setups.py`), picked by
 * `?setup=`: an up arrow where its long position opened, a down arrow where its own exit rule closed
 * it, with the lines that rule reads drawn beside them. The package walks the positions; the page
 * only places what the file says. The scan-rule matches are listed below the chart, not drawn on it.
 */

const UP = "#43b57a";
const DOWN = "#d95c4a";
const GRID_INK = "#8a93a3";
const OURS = SERIES_COLORS[0]!;
const VENDOR = SERIES_COLORS[2]!;
/** Both arrows are amber: an arrow marks an event, and green or red would read as a gain or a loss. */
const ARROW = SERIES_COLORS[2]!;

/** How each series a setup reads is drawn, keyed as the chart file's `setup_lines`. */
const SETUP_LINE: Record<string, { title: string; color: string; style: LineStyle; width: 1 | 2 }> = {
  ema9: { title: "EMA 9", color: SERIES_COLORS[4]!, style: LineStyle.Solid, width: 1 },
  ema21: { title: "EMA 21", color: SERIES_COLORS[0]!, style: LineStyle.Solid, width: 2 },
  ema50: { title: "EMA 50", color: SERIES_COLORS[3]!, style: LineStyle.Solid, width: 2 },
  bb_upper: { title: "BB upper", color: GRID_INK, style: LineStyle.Dotted, width: 1 },
  bb_mid: { title: "BB mid", color: GRID_INK, style: LineStyle.Dashed, width: 1 },
  bb_lower: { title: "BB lower", color: GRID_INK, style: LineStyle.Dotted, width: 1 },
  supertrend: { title: "Supertrend", color: SERIES_COLORS[5]!, style: LineStyle.Solid, width: 2 },
};

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

const CHIP_H = 17;
const CHIP_PAD = 5;

/** Black or white text over a chip, by the chip colour's luminance. */
function chipInk(hex: string): string {
  const n = Number.parseInt(hex.slice(1), 16);
  const lum = 0.299 * ((n >> 16) & 255) + 0.587 * ((n >> 8) & 255) + 0.114 * (n & 255);
  return lum > 160 ? "#000000" : "#ffffff";
}

interface LevelTitle {
  price: number;
  title: string;
  color: string;
  /** Where its line starts, for a line that starts mid-chart; the chip sits there, not at the edge. */
  from?: Time;
}

/** Which horizontal levels are drawn: every one, only those the vendor's own chart draws, or none. */
type LevelsMode = "all" | "vendor" | "off";
const LEVELS_MODES: readonly LevelsMode[] = ["all", "vendor", "off"];
const LEVELS_LABEL: Record<LevelsMode, string> = { all: "All levels", vendor: "Vendor's view", off: "Off" };

/** A series' last drawn value, for its chip; null when it has none. */
function lastValue(values: readonly (number | null)[]): number | null {
  for (let i = values.length - 1; i >= 0; i--) {
    const v = values[i];
    if (v !== null && v !== undefined) return v;
  }
  return null;
}

/**
 * Titles and values drawn as chips at the pane's LEFT edge: a price line's, or a series' at its last
 * value. The library draws them against the price axis, where they sit over the latest bars and the
 * scale's own numbers; the lines and series here carry neither. Every entry is placed on the scale of
 * the series the primitive is attached to, so each pane gets one primitive on one of its series.
 */
class LeftTitles implements ISeriesPrimitive<Time> {
  private series: ISeriesApi<SeriesType> | null = null;
  private chart: IChartApi | null = null;
  private readonly views: readonly IPrimitivePaneView[];

  constructor(private readonly titles: LevelTitle[]) {
    const renderer: IPrimitivePaneRenderer = { draw: (target) => this.draw(target) };
    // Over the series, under the setup arrows (which draw "top"): an arrow on an early bar is information,
    // the chip over it is only a label.
    this.views = [{ zOrder: () => "normal", renderer: () => renderer }];
  }

  attached(param: SeriesAttachedParameter<Time>): void {
    this.series = param.series;
    this.chart = param.chart as IChartApi;
  }

  detached(): void {
    this.series = null;
    this.chart = null;
  }

  paneViews(): readonly IPrimitivePaneView[] {
    return this.views;
  }

  private draw(target: Parameters<IPrimitivePaneRenderer["draw"]>[0]): void {
    const series = this.series;
    if (series === null) return;
    target.useMediaCoordinateSpace(({ context, mediaSize }) => {
      context.font = "11px -apple-system, BlinkMacSystemFont, 'Trebuchet MS', Roboto, Ubuntu, sans-serif";
      context.textBaseline = "middle";
      // A chip centred on its line, as the library draws a title. A vendor level on the grid's high
      // or low shares its line; chips that would overlap sit side by side instead.
      const placed: { y: number; left: number; right: number }[] = [];
      for (const l of this.titles) {
        const y = series.priceToCoordinate(l.price);
        if (y === null) continue;
        const start = l.from === undefined ? null : (this.chart?.timeScale().timeToCoordinate(l.from) ?? null);
        // At its line's start (or the left edge), else just right of a chip it would cover, else just
        // left of one -- always inside the pane, so a line that starts near the right edge keeps a
        // readable chip rather than one drawn over its neighbour's.
        const texts = [l.title, fmt(l.price)];
        const total = texts.reduce((sum, text) => sum + context.measureText(text).width + 2 * CHIP_PAD + 1, -1);
        const fit = (v: number) => Math.max(0, Math.min(v, mediaSize.width - total));
        const row = placed.filter((p) => Math.abs(p.y - y) < CHIP_H);
        const clear = (v: number) => !row.some((p) => v < p.right + 4 && v + total + 4 > p.left);
        const tries = [start ?? 0, ...row.map((p) => p.right + 4), ...row.map((p) => p.left - total - 4)].map(fit);
        let x = tries.find(clear) ?? fit(start ?? 0);
        const left = x;
        for (const text of texts) {
          const w = context.measureText(text).width + 2 * CHIP_PAD;
          context.fillStyle = l.color;
          context.beginPath();
          context.roundRect(x, Math.round(y - CHIP_H / 2), w, CHIP_H, 2);
          context.fill();
          context.fillStyle = chipInk(l.color);
          context.fillText(text, x + CHIP_PAD, y + 0.5);
          x += w + 1;
        }
        placed.push({ y, left, right: x - 1 });
      }
    });
  }
}

function PriceChart({ c, setup, levels }: { c: TechnicalsChart; setup: TechnicalsSetup | null; levels: LevelsMode }) {
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

    // Pane 0: candles, our grid's extremes, the vendor's levels, the chosen setup's lines and arrows.
    const candles = chart.addSeries(CandlestickSeries, {
      upColor: UP,
      downColor: DOWN,
      borderVisible: false,
      wickUpColor: UP,
      wickDownColor: DOWN,
      priceLineVisible: false,
    });
    candles.setData(c.bars.map((b) => ({ time: t(b.date), open: b.open, high: b.high, low: b.low, close: b.close })));
    const titles: LevelTitle[] = [];
    // "All" draws our grid's extremes and every vendor level; "vendor" only the levels the vendor's own
    // chart draws (the file's `vendorView`, a package rule; a file without it shows them all); "off"
    // none. Never the setup lines, which are what the arrows are read against.
    if (levels === "all" && c.grid) {
      for (const [price, title] of [
        [c.grid.high, "grid high"],
        [c.grid.low, "grid low"],
      ] as const) {
        candles.createPriceLine({ price, color: GRID_INK, lineWidth: 1, lineStyle: LineStyle.LargeDashed, axisLabelVisible: false, title: "" });
        titles.push({ price, title, color: GRID_INK });
      }
    }
    const drawn = (c.vendor?.levels ?? []).filter((l) => levels === "all" || (levels === "vendor" && l.vendorView !== false));
    for (const l of drawn) {
      // As the vendor draws it: from the level's own date to the last bar, so a level set last week
      // is a short line, not one across the whole chart. Kept out of the autoscale, as a price line
      // would be, so a far-off level does not squash the candles.
      const from = l.date !== null && l.date > dates[0]! ? (dates.find((d) => d >= l.date!) ?? dates[0]!) : dates[0]!;
      const color = levelColor(l);
      const s = chart.addSeries(LineSeries, {
        color,
        lineWidth: 1,
        lineStyle: levelStyle(l),
        title: "",
        lastValueVisible: false,
        priceLineVisible: false,
        crosshairMarkerVisible: false,
        autoscaleInfoProvider: () => null,
      });
      s.setData(dates.filter((d) => d >= from).map((d) => ({ time: t(d), value: l.value })));
      titles.push({ price: l.value, title: `vendor ${KIND_LABEL[l.kind] ?? l.kind}`, color, from: from === dates[0] ? undefined : t(from) });
    }
    // The chips draw with whichever pane-0 series carries them, so they go on the last one added:
    // over every line, and under the arrows, which draw on top.
    let topmost: ISeriesApi<SeriesType> = candles;
    for (const key of setup?.lines ?? []) {
      const look = SETUP_LINE[key];
      const values = c.setupLines[key];
      if (look === undefined || values === undefined) continue;
      const s = chart.addSeries(LineSeries, {
        color: look.color,
        lineWidth: look.width,
        lineStyle: look.style,
        title: "",
        lastValueVisible: false,
        priceLineVisible: false,
        crosshairMarkerVisible: false,
      });
      s.setData(line(dates, values));
      topmost = s;
      const last = lastValue(values);
      if (last !== null) titles.push({ price: last, title: look.title, color: look.color });
    }
    topmost.attachPrimitive(new LeftTitles(titles));
    // An entry is an up arrow under its bar; an exit a down arrow over its bar, labelled with what
    // closed it. A position entered before the bars drawn shows only its exit.
    const inRange = new Set(dates);
    const markers: SeriesMarker<Time>[] = [];
    for (const tr of setup?.trades ?? []) {
      if (inRange.has(tr.entryDate)) {
        markers.push({ time: t(tr.entryDate), position: "belowBar", color: ARROW, shape: "arrowUp", text: "" });
      }
      if (tr.exitDate !== null && inRange.has(tr.exitDate)) {
        markers.push({ time: t(tr.exitDate), position: "aboveBar", color: ARROW, shape: "arrowDown", text: tr.reason ?? "" });
      }
    }
    markers.sort((a, b) => (a.time as number) - (b.time as number));
    // Above the chips (LeftTitles draws "normal"), so an arrow on an early bar is never hidden by one.
    createSeriesMarkers(candles, markers, { zOrder: "aboveSeries" });

    // Series titles and last values are chips at the left (LeftTitles), not on the axis.
    const seriesChip = (values: readonly (number | null)[], title: string, color: string): LevelTitle[] => {
      const price = lastValue(values);
      return price === null ? [] : [{ price, title, color }];
    };
    const unlabelled = { title: "", lastValueVisible: false, priceLineVisible: false } as const;

    // Pane 1: RSI 14, the reading the setups use (mean reversion under 30, the pullback's 40-50 dip),
    // on a fixed 0-100 scale so 30 and 70 are always on screen; 50 is the midline.
    const rsi = chart.addSeries(
      LineSeries,
      { color: OURS, lineWidth: 2, ...unlabelled, autoscaleInfoProvider: () => ({ priceRange: { minValue: 0, maxValue: 100 } }) },
      1,
    );
    rsi.setData(line(dates, c.rsi14));
    rsi.priceScale().applyOptions({ scaleMargins: { top: 0.08, bottom: 0.08 } });
    for (const [price, style] of [
      [70, LineStyle.Dotted],
      [50, LineStyle.Dashed],
      [30, LineStyle.Dotted],
    ] as const) {
      rsi.createPriceLine({ price, color: GRID_INK, lineWidth: 1, lineStyle: style, axisLabelVisible: false, title: "" });
    }
    rsi.attachPrimitive(new LeftTitles(seriesChip(c.rsi14, "RSI 14", OURS)));

    // Pane 2: the short-term trend score, ours against the vendor's grade for the same day.
    const ours = chart.addSeries(LineSeries, { color: OURS, lineWidth: 2, lineType: 1, ...unlabelled }, 2);
    ours.setData(line(dates, c.trendShort));
    const trendTitles = seriesChip(c.trendShort, "trend (ours)", OURS);
    if (c.vendor && c.vendor.trendShort.length > 0) {
      const first = dates[0]!;
      const vendorTrend = c.vendor.trendShort.filter((p) => p.date >= first);
      const theirs = chart.addSeries(LineSeries, { color: VENDOR, lineWidth: 2, lineType: 1, lineStyle: LineStyle.Dashed, ...unlabelled }, 2);
      theirs.setData(vendorTrend.map((p) => ({ time: t(p.date), value: p.value })));
      trendTitles.push(...seriesChip(vendorTrend.map((p) => p.value), "trend (vendor)", VENDOR));
    }
    ours.attachPrimitive(new LeftTitles(trendTitles));

    // Relative, not pixel, heights: a pixel height set before the first layout is overridden.
    const panes = chart.panes();
    panes[0]?.setStretchFactor(0.6);
    panes[1]?.setStretchFactor(0.22);
    panes[2]?.setStretchFactor(0.18);
    chart.timeScale().fitContent();
    return () => chart.remove();
  }, [c, setup, levels]);

  if (c.bars.length < 2) return <p className="muted">Not enough bars to draw.</p>;
  return <div ref={hostRef} style={{ height: "620px" }} />;
}

/**
 * The setup picker, the chosen setup's rule as the package words it, and whether it holds a position.
 * Where the setups read another symbol's volume (SPX reads SPY's) it says so, whatever setup is shown.
 */
function LevelsToggle({ c, mode, onChange }: { c: TechnicalsChart; mode: LevelsMode; onChange: (m: LevelsMode) => void }) {
  const all = (c.grid ? 2 : 0) + (c.vendor?.levels.length ?? 0);
  const theirs = c.vendor?.levels.filter((l) => l.vendorView !== false).length ?? 0;
  const tip: Record<LevelsMode, string> = {
    all: `Our grid's high and low and every level in the vendor's data: ${all} lines`,
    vendor: `Only what the vendor's own chart draws, the two nearest supports and resistances: ${theirs} lines`,
    off: "No levels",
  };
  // Without a vendor capture there is no vendor's view to show.
  const modes = LEVELS_MODES.filter((m) => m !== "vendor" || c.vendor !== null);
  return (
    <div className="mode-toggle" role="group" aria-label="levels" style={{ marginLeft: 0 }}>
      {modes.map((m) => (
        <button key={m} type="button" title={tip[m]} className={m === mode ? "mode-btn active" : "mode-btn"} onClick={() => onChange(m)}>
          {LEVELS_LABEL[m]}
        </button>
      ))}
    </div>
  );
}

function SetupBar({
  c,
  setup,
  onPick,
  levels,
  onLevels,
}: {
  c: TechnicalsChart;
  setup: TechnicalsSetup | null;
  onPick: (id: string) => void;
  levels: LevelsMode;
  onLevels: (m: LevelsMode) => void;
}) {
  if (c.setups.length === 0) {
    return (
      <div style={{ display: "flex", flexWrap: "wrap", gap: 8, alignItems: "center", marginBottom: 8 }}>
        <LevelsToggle c={c} mode={levels} onChange={onLevels} />
        <span className="muted">This chart file predates the entry/exit setups; the next report run writes them.</span>
      </div>
    );
  }
  const open = setup?.trades.find((tr) => tr.exitDate === null);
  const shown = new Set(c.bars.map((b) => b.date));
  const entries = (setup?.trades ?? []).filter((tr) => shown.has(tr.entryDate)).length;
  return (
    <div style={{ marginBottom: 8 }}>
      <div style={{ display: "flex", flexWrap: "wrap", gap: 8, alignItems: "center" }}>
        <div className="mode-toggle" role="group" aria-label="setup" style={{ marginLeft: 0 }}>
          {c.setups.map((s) => (
            <button key={s.id} type="button" className={s.id === setup?.id ? "mode-btn active" : "mode-btn"} onClick={() => onPick(s.id)}>
              {s.name}
            </button>
          ))}
        </div>
        <LevelsToggle c={c} mode={levels} onChange={onLevels} />
        {setup && (
          <span className="chip">
            {entries} {entries === 1 ? "entry" : "entries"} in {c.bars.length} sessions
          </span>
        )}
        {open && (
          <span className="chip" title="The position this setup opened has not met its exit rule yet.">
            open since {open.entryDate} at {fmt(open.entryPrice)}
            {open.target !== null ? `, target ${fmt(open.target)}` : ""}
          </span>
        )}
      </div>
      {setup && (
        <p className="muted" style={{ marginTop: 6 }}>
          {setup.rule}
        </p>
      )}
      {c.volumeSource !== null && (
        <p className="muted" style={{ marginTop: 4 }}>
          <strong>Volume is {c.volumeSource}'s.</strong> {c.symbol} has no volume of its own: it is calculated from its
          member stocks, so nothing trades as {c.symbol}. The breakout setup's volume test reads {c.volumeSource}'s
          volume for the same session, against {c.volumeSource}'s own 50-session average.
        </p>
      )}
    </div>
  );
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
          <span className="stat-label muted">1–10; ours {c.rank ?? "—"}</span>
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
              <th title="Whether the vendor's own chart page draws it: the two nearest of its support list and of its resistance list, never a gap level.">
                On their chart
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
                  <td className={l.vendorView === true ? "" : "muted"}>{l.vendorView === null ? "—" : l.vendorView ? "yes" : "no"}</td>
                </tr>
              ))}
          </tbody>
        </table>
      )}
      <p className="muted">
        On the chart: solid, a support or resistance our grid places; dotted, a gap level that is an edge of one
        of our gaps; dashed, a level we cannot produce. Each starts at its own date, as the vendor draws it.
        "Vendor's view" shows only the levels the vendor's chart page draws (the two nearest supports and
        resistances, no gaps); which levels the vendor computes in the first place is not yet reproduced.
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
      <p className="muted">
        The vendor's scan rules as we reproduce them: a pattern, not a trade idea, so they are listed here and not
        drawn. The chart's arrows are the selected setup's entries and exits.
      </p>
    </Card>
  );
}

/**
 * What the tab opens on when the URL names no symbol: the index itself. Dolt carries no cash
 * indexes, so SPX's chart is built from the broker's daily candles (scripts/fetch_index_bars.py,
 * landed by packages/technicals since 2026-10-01). It has no volume, no rank and no vendor chart.
 */
const DEFAULT_SYMBOL = "SPX";

export function ChartPage() {
  const [params, setParams] = useSearchParams();
  const symbol = params.get("symbol")?.toUpperCase() || DEFAULT_SYMBOL;
  const setupParam = params.get("setup");
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
  // The first setup (trend following) unless the URL names another the file carries.
  const setup = c ? (c.setups.find((s) => s.id === setupParam) ?? c.setups[0] ?? null) : null;
  const pickSetup = (id: string) => {
    const next = new URLSearchParams(params);
    next.set("setup", id);
    setParams(next, { replace: true });
  };
  // The vendor's view where the vendor's chart was captured, else every level (our grid's two); a
  // `?levels=` in the URL outranks that, so a link carries the view it was sent with.
  const levelsParam = params.get("levels");
  const fallback: LevelsMode = c?.vendor ? "vendor" : "all";
  const asked = LEVELS_MODES.find((m) => m === levelsParam);
  const levels: LevelsMode = asked === undefined || (asked === "vendor" && !c?.vendor) ? fallback : asked;
  const setLevels = (m: LevelsMode) => {
    const next = new URLSearchParams(params);
    if (m === fallback) next.delete("levels");
    else next.set("levels", m);
    setParams(next, { replace: true });
  };

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
          <Card title="Price, RSI and trend" asOf={last ? `close ${fmt(last.close)}` : undefined}>
            <SetupBar c={c} setup={setup} onPick={pickSetup} levels={levels} onLevels={setLevels} />
            <PriceChart c={c} setup={setup} levels={levels} />
            <p className="muted">
              {c.grid
                ? `Grid over ${c.grid.window ?? 250} sessions: low ${fmt(c.grid.low)} (${c.grid.lowDate ?? "—"}), high ${fmt(c.grid.high)} (${c.grid.highDate ?? "—"}), step ${fmt(c.grid.step)}. `
                : "Too few sessions for a grid. "}
              Arrows: up where the setup's long position opened, down where its exit rule closed it, labelled
              with what closed it. Middle pane: RSI 14 with 30, 50 and 70. Bottom: the short-term trend score (−4 to +4), ours solid
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
