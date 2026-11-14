import { useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import {
  createChart,
  CandlestickSeries,
  HistogramSeries,
  type CandlestickData,
  type HistogramData,
  type IPriceLine,
  type ISeriesApi,
  type Time,
  type UTCTimestamp,
  type WhitespaceData,
} from "lightweight-charts";
import { CANDLE_PERIODS, type CandleBar, type CandlePeriod, type FuturesTickerEntry } from "@console/shared";
import { useFuturesTicker } from "../../lib/api";
import { useCandles } from "../../lib/useCandles";
import { useWsState } from "../../lib/useQuote";
import { wsClient } from "../../lib/wsClient";
import { canvasPalette as _pal, hexOf } from "../../components/chart/cssvars";
import { tokenAlpha } from "../../components/chart/cssvars";
import type { MarketProfile } from "../../lib/marketProfile";
import {
  dashFor,
  LINE_LEGEND,
  lineStyleFor,
  ProfileHistogram,
  profileLines,
  sessionProfiles,
  type OverlayColours,
  type SessionProfiles,
} from "./profileOverlay";

const PAL = _pal();

/**
 * A live intraday chart: 1-, 5- or 15-minute candles over the console's own DXLink session, extended
 * hours included, the bar in progress moving as the tape trades. Futures and four cash symbols
 * (SPX, SPY, NDX, QQQ).
 *
 * A future's contract is the futures ticker's (`state/futures_contracts.json`, the broker's answer),
 * never assembled here; a cash symbol is its own DXLink symbol. History arrives whole when the feed's
 * snapshot ends; after that each message is the bar in progress, or a new one. The candles are drawn
 * as the feed sent them. The Market Profile overlay (`profileOverlay.ts`) is the one derivation, a
 * mirror of `cherrypick.core.marketprofile` pinned by the shared fixture.
 */

/** Cash symbols the chart offers beside the futures, profiled over the same regular session. An
 *  index (SPX, NDX) carries no volume, so its profile is TPO only. */
export const CASH_INSTRUMENTS: FuturesTickerEntry[] = [
  { product: "SPX", label: "SPX", name: "S&P 500 index", contract: null, streamerSymbol: "SPX", expiration: null, reason: null },
  { product: "SPY", label: "SPY", name: "S&P 500 ETF", contract: null, streamerSymbol: "SPY", expiration: null, reason: null },
  { product: "NDX", label: "NDX", name: "Nasdaq-100 index", contract: null, streamerSymbol: "NDX", expiration: null, reason: null },
  { product: "QQQ", label: "QQQ", name: "Nasdaq-100 ETF", contract: null, streamerSymbol: "QQQ", expiration: null, reason: null },
];
const isCash = (product: string): boolean => CASH_INSTRUMENTS.some((e) => e.product === product);

const OVERLAY: OverlayColours = {
  poc: PAL.amber,
  va: PAL.accent,
  muted: PAL.textMuted,
  rowFill: tokenAlpha("--text-muted", 0.14),
  vaFill: tokenAlpha("--accent", 0.22),
  pocFill: tokenAlpha("--amber-line", 0.45),
};

const UP = hexOf("--pos", "#3af3a0");
const DOWN = hexOf("--neg", "#ee6760");
const UP_VOL = tokenAlpha("--pos", 0.35);
const DOWN_VOL = tokenAlpha("--neg", 0.35);
/** Bars in view when the history lands; the rest is a scroll away. */
const INITIAL_BARS = 150;

const ET = "America/New_York";
const ET_TIME = new Intl.DateTimeFormat("en-US", { timeZone: ET, hour: "2-digit", minute: "2-digit", hourCycle: "h23" });
const ET_SECONDS = new Intl.DateTimeFormat("en-US", {
  timeZone: ET,
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
  hourCycle: "h23",
});
const ET_DAY = new Intl.DateTimeFormat("en-US", { timeZone: ET, month: "short", day: "numeric" });
const ET_STAMP = new Intl.DateTimeFormat("en-US", {
  timeZone: ET,
  weekday: "short",
  month: "short",
  day: "numeric",
  hour: "2-digit",
  minute: "2-digit",
  hourCycle: "h23",
});

/**
 * The time axis on the session's clock, wherever the viewer sits. The library places its day ticks
 * at UTC midnight, which is 19:00 or 20:00 ET, so a day tick is labelled with its ET time and only
 * ET midnight carries a date; dating the UTC tick put "Oct 1" at 20:00 on Oct 1.
 */
export function etTickMark(time: Time): string {
  const ms = (time as number) * 1000;
  const hm = ET_TIME.format(ms);
  return hm === "00:00" ? ET_DAY.format(ms) : hm;
}

/** Decimals a product's price needs: bond futures trade in 32nds. */
const PRECISION: Record<string, number> = { ZB: 5 };

const toCandle = (b: CandleBar): CandlestickData<Time> => ({
  time: b.t as UTCTimestamp,
  open: b.o,
  high: b.h,
  low: b.l,
  close: b.c,
});
/** A bar with no volume (every bar of an index: SPX, NDX) is left blank, not drawn as a zero-height bar,
 *  which ran along the bottom of the chart as a stray unexplained line. */
const toVolume = (b: CandleBar): HistogramData<Time> | WhitespaceData<Time> =>
  b.v > 0 ? { time: b.t as UTCTimestamp, value: b.v, color: b.c >= b.o ? UP_VOL : DOWN_VOL } : { time: b.t as UTCTimestamp };

const isPeriod = (p: string | null): p is CandlePeriod => p !== null && (CANDLE_PERIODS as readonly string[]).includes(p);

interface ChartInfo {
  loaded: boolean;
  count: number;
  last: CandleBar | null;
  updatedAt: number | null;
}

const SESSION_DAY = new Intl.DateTimeFormat("en-US", { timeZone: "UTC", month: "short", day: "numeric" });
const sessionLabel = (iso: string): string => SESSION_DAY.format(Date.parse(`${iso}T12:00:00Z`));

function ProfileReadout({ profiles, precision }: { profiles: SessionProfiles; precision: number }) {
  const { latest, prior, developing } = profiles;
  if (latest === null) return <div className="intraday-status muted">profile: no regular-session bars yet</div>;
  const f = (x: number): string => x.toFixed(precision);
  const one = (p: MarketProfile, label: string) => (
    <span>
      {label} <strong>POC {f(p.poc)}</strong> · VA {f(p.val)}–{f(p.vah + p.rowSize)}
    </span>
  );
  const periods = latest.periods.length > 1 ? `${latest.periods[0]}–${latest.periods.at(-1)}` : latest.periods;
  return (
    <div className="intraday-status muted">
      {one(latest, `${sessionLabel(latest.session)}${developing ? ` (developing, ${periods})` : ""}:`)}
      {latest.ibLow !== null && latest.ibHigh !== null && (
        <span>
          {" "}
          · IB {f(latest.ibLow)}–{f(latest.ibHigh)}
        </span>
      )}
      <span> · {latest.volumeTotal > 0 && latest.volumePoc !== null ? `vPOC ${f(latest.volumePoc)}` : "TPO only (no volume)"}</span>
      <span> · rows of {latest.rowSize}</span>
      {prior !== null && <span> · {one(prior, `prior ${sessionLabel(prior.session)}:`)}</span>}
    </div>
  );
}

function LiveCandles({
  entry,
  period,
  profile,
}: {
  entry: FuturesTickerEntry & { streamerSymbol: string };
  period: CandlePeriod;
  profile: boolean;
}) {
  const hostRef = useRef<HTMLDivElement>(null);
  const [info, setInfo] = useState<ChartInfo>({ loaded: false, count: 0, last: null, updatedAt: null });
  const ws = useWsState();
  const symbol = entry.streamerSymbol;
  const seriesRef = useRef<ISeriesApi<"Candlestick", Time> | null>(null);
  const histRef = useRef<ProfileHistogram | null>(null);
  const linesRef = useRef<{ key: string; lines: IPriceLine[] }>({ key: "", lines: [] });

  // The profile reads the 15-minute series whatever width is drawn (the same series when it is 15m).
  const profileBars = useCandles(profile ? symbol : "", "15m");
  const profiles = useMemo(() => sessionProfiles(profileBars), [profileBars]);

  useEffect(() => {
    const el = hostRef.current;
    if (el === null) return;
    const precision = PRECISION[entry.product] ?? 2;
    const chart = createChart(el, {
      autoSize: true,
      layout: { background: { color: "transparent" }, textColor: PAL.textMuted },
      grid: { vertLines: { color: PAL.grid }, horzLines: { color: PAL.grid } },
      rightPriceScale: { borderColor: PAL.gridMid },
      timeScale: {
        borderColor: PAL.gridMid,
        timeVisible: true,
        secondsVisible: false,
        rightOffset: 5,
        tickMarkFormatter: etTickMark,
      },
      localization: { timeFormatter: (time: Time) => `${ET_STAMP.format((time as number) * 1000)} ET` },
    });
    const candles = chart.addSeries(CandlestickSeries, {
      upColor: UP,
      downColor: DOWN,
      borderVisible: false,
      wickUpColor: UP,
      wickDownColor: DOWN,
      priceFormat: { type: "price", precision, minMove: 10 ** -precision },
    });
    candles.priceScale().applyOptions({ scaleMargins: { top: 0.05, bottom: 0.25 } });
    const volume = chart.addSeries(HistogramSeries, {
      priceScaleId: "",
      priceFormat: { type: "volume" },
      lastValueVisible: false,
      priceLineVisible: false,
    });
    volume.priceScale().applyOptions({ scaleMargins: { top: 0.8, bottom: 0 } });
    const histogram = new ProfileHistogram(OVERLAY);
    candles.attachPrimitive(histogram);
    seriesRef.current = candles;
    histRef.current = histogram;
    linesRef.current = { key: "", lines: [] };

    let lastT = -Infinity;
    let framed = false;
    const note = (bars: CandleBar[], loaded: boolean): void =>
      setInfo({ loaded, count: bars.length, last: bars.at(-1) ?? null, updatedAt: Date.now() });

    const drawAll = (): void => {
      const bars = wsClient.getCandles(symbol, period);
      candles.setData(bars.map(toCandle));
      volume.setData(bars.map(toVolume));
      lastT = bars.at(-1)?.t ?? -Infinity;
      if (!framed && bars.length > 0) {
        framed = true;
        chart.timeScale().setVisibleLogicalRange({ from: bars.length - INITIAL_BARS, to: bars.length + 5 });
      }
      note(bars, true);
    };

    const off = wsClient.onCandles(symbol, period, (msg) => {
      // A bar older than the newest drawn cannot go through update(); redraw the series instead.
      if (msg.replace || msg.bars.some((b) => b.t < lastT)) {
        drawAll();
        return;
      }
      for (const b of msg.bars) {
        candles.update(toCandle(b));
        volume.update(toVolume(b));
        lastT = b.t;
      }
      setInfo({
        loaded: true,
        count: wsClient.candleCount(symbol, period),
        last: msg.bars.at(-1) ?? null,
        updatedAt: Date.now(),
      });
    });
    wsClient.acquireCandles(symbol, period);
    // Another holder of this series may already have its bars.
    if (wsClient.getCandles(symbol, period).length > 0) drawAll();

    return () => {
      off();
      wsClient.releaseCandles(symbol, period);
      seriesRef.current = null;
      histRef.current = null;
      linesRef.current = { key: "", lines: [] };
      chart.remove();
    };
  }, [symbol, period, entry.product]);

  // The overlay follows the profile; price lines are rebuilt only when a level actually moves (the
  // 15-minute bar in progress is re-sent about once a second).
  useEffect(() => {
    const series = seriesRef.current;
    histRef.current?.setProfile(profile ? profiles.latest : null);
    if (series === null) return;
    const want = profile ? profileLines(profiles) : [];
    const key = want.map((l) => `${l.key}:${l.price}`).join("|");
    if (key === linesRef.current.key) return;
    for (const line of linesRef.current.lines) series.removePriceLine(line);
    const lines = want.map((l) => series.createPriceLine({ price: l.price, title: l.title, ...lineStyleFor(l.role, OVERLAY) }));
    linesRef.current = { key, lines };
  }, [profile, profiles]);

  const live = ws.dxlink === "connected";
  const precision = PRECISION[entry.product] ?? 2;
  return (
    <>
      <div className="intraday-status muted" aria-live="polite">
        {!info.loaded ? (
          <span>loading history…</span>
        ) : info.last === null ? (
          <span>the feed sent no bars for this contract</span>
        ) : (
          <>
            <span>
              last <strong>{info.last.c.toFixed(precision)}</strong>
            </span>
            <span> · bar {ET_TIME.format(info.last.t * 1000)} ET</span>
            {info.updatedAt !== null && <span> · updated {ET_SECONDS.format(info.updatedAt)} ET</span>}
            <span> · {info.count} bars</span>
          </>
        )}
        {!live && <span className="chip" style={{ marginLeft: 8 }}>feed {ws.dxlink} — not live</span>}
      </div>
      {profile && profileBars.length > 0 && <ProfileReadout profiles={profiles} precision={precision} />}
      <div ref={hostRef} style={{ height: "560px" }} />
      {profile && profileBars.length > 0 && <ProfileLegend />}
    </>
  );
}

/** What each horizontal line is: a swatch drawn in the line's own colour and stroke, and its meaning. */
function ProfileLegend() {
  return (
    <ul
      aria-label="profile lines"
      style={{ listStyle: "none", margin: "8px 0 0", padding: 0, display: "flex", flexWrap: "wrap", gap: "4px 18px", fontSize: "0.85em" }}
    >
      {LINE_LEGEND.map(({ role, label, meaning }) => {
        const s = lineStyleFor(role, OVERLAY);
        return (
          <li key={role} style={{ display: "flex", alignItems: "center", gap: 6, minWidth: 0 }}>
            <svg width="28" height="8" aria-hidden="true" style={{ flex: "none" }}>
              <line x1="0" y1="4" x2="28" y2="4" stroke={s.color} strokeWidth="2" strokeDasharray={dashFor(s.lineStyle)} />
            </svg>
            <strong>{label}</strong>
            <span className="muted">{meaning}</span>
          </li>
        );
      })}
    </ul>
  );
}

function Toggle<T extends string>({
  value,
  options,
  onChange,
  label,
  render,
}: {
  value: T;
  options: readonly T[];
  onChange: (v: T) => void;
  label: string;
  render?: (v: T) => string;
}) {
  return (
    <div className="mode-toggle" role="group" aria-label={label}>
      {options.map((o) => (
        <button key={o} type="button" className={value === o ? "mode-btn active" : "mode-btn"} onClick={() => onChange(o)}>
          {render ? render(o) : o}
        </button>
      ))}
    </div>
  );
}

export function IntradayPage() {
  const [params, setParams] = useSearchParams();
  const { data, isError } = useFuturesTicker();
  const product = params.get("product") ?? "ES";
  const periodParam = params.get("period");
  const period: CandlePeriod = isPeriod(periodParam) ? periodParam : "5m";
  const set = (key: string, value: string): void => {
    const next = new URLSearchParams(params);
    next.set(key, value);
    setParams(next, { replace: true });
  };

  const profile = params.get("profile") !== "off";

  const entries = [...CASH_INSTRUMENTS, ...(data?.entries ?? [])];
  const entry = entries.find((e) => e.product === product);
  const products = entries.map((e) => e.product);
  const labelOf = (p: string): string => (isCash(p) ? p : `/${p}`);

  return (
    <section className="card">
      <div className="card-head">
        <h2>
          {entry?.label ?? labelOf(product)} {entry?.contract !== null && entry?.contract !== undefined ? `(${entry.contract})` : ""} ·{" "}
          {period} candles
        </h2>
        <span className="chip">chart only</span>
        <div style={{ marginLeft: "auto", display: "flex", flexWrap: "wrap", gap: 8, minWidth: 0 }}>
          <Toggle value={product} options={products} onChange={(v) => set("product", v)} label="symbol" render={labelOf} />
          <Toggle value={period} options={CANDLE_PERIODS} onChange={(v) => set("period", v)} label="candle width" />
          <Toggle
            value={profile ? "on" : "off"}
            options={["on", "off"] as const}
            onChange={(v) => set("profile", v)}
            label="market profile"
            render={(v) => `profile ${v}`}
          />
        </div>
      </div>
      {entry === undefined ? (
        data === undefined && !isError ? (
          <p className="muted">loading…</p>
        ) : data === undefined ? (
          <p className="muted">Could not read the futures contract map from the console server.</p>
        ) : (
          <p className="muted">
            {labelOf(product)} is not one of the chart's symbols ({products.map(labelOf).join(", ")}).
          </p>
        )
      ) : entry.streamerSymbol === null ? (
        <p className="muted">No contract for /{product}: {entry.reason ?? "unknown"} (see scripts/refresh_futures_contracts.py).</p>
      ) : (
        <LiveCandles
          key={`${entry.streamerSymbol}|${period}`}
          entry={{ ...entry, streamerSymbol: entry.streamerSymbol }}
          period={period}
          profile={profile}
        />
      )}
      <p className="muted" style={{ fontSize: "0.85em", marginTop: 8 }}>
        Extended hours, times in ET. CME futures trade Sunday 18:00 to Friday 17:00 ET with a daily break from 17:00
        to 18:00; the axis skips periods with no bars, so the break and weekends close up rather than showing as
        gaps. The bar in progress updates about once a second while the contract trades.
      </p>
      {profile && (
        <p className="muted" style={{ fontSize: "0.85em", marginTop: 4 }}>
          Market Profile: the regular session, 09:30–16:00 ET, for every symbol (a future's overnight is left out), in
          30-minute periods A–M, built from 15-minute candles. The shaded histogram is the newest session's time at
          each price; the brighter rows are its value area (70% of the time) and the amber row its point of control.
          Each line is named on the price axis and in the legend under the chart; the coloured line that ends in
          the price itself is the last trade. SPX and NDX carry no volume, so theirs is time only. Chart only: no
          module reads it.
        </p>
      )}
    </section>
  );
}
