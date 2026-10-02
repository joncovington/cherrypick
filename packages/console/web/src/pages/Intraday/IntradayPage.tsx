import { useEffect, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import {
  createChart,
  CandlestickSeries,
  HistogramSeries,
  type CandlestickData,
  type HistogramData,
  type Time,
  type UTCTimestamp,
} from "lightweight-charts";
import { CANDLE_PERIODS, type CandleBar, type CandlePeriod, type FuturesTickerEntry } from "@console/shared";
import { useFuturesTicker } from "../../lib/api";
import { useWsState } from "../../lib/useQuote";
import { wsClient } from "../../lib/wsClient";

/**
 * A live intraday futures chart: 1-, 5- or 15-minute candles over the console's own DXLink session,
 * extended hours included, the bar in progress moving as the tape trades.
 *
 * The contract is the futures ticker's (`state/futures_contracts.json`, the broker's answer), never
 * assembled here. History arrives whole when the feed's snapshot ends; after that each message is the
 * bar in progress, or a new one. The chart draws what the feed sent and computes nothing.
 */

const UP = "#43b57a";
const DOWN = "#d95c4a";
const UP_VOL = "rgba(67, 181, 122, 0.35)";
const DOWN_VOL = "rgba(217, 92, 74, 0.35)";
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
const toVolume = (b: CandleBar): HistogramData<Time> => ({
  time: b.t as UTCTimestamp,
  value: b.v,
  color: b.c >= b.o ? UP_VOL : DOWN_VOL,
});

const isPeriod = (p: string | null): p is CandlePeriod => p !== null && (CANDLE_PERIODS as readonly string[]).includes(p);

interface ChartInfo {
  loaded: boolean;
  count: number;
  last: CandleBar | null;
  updatedAt: number | null;
}

function LiveCandles({ entry, period }: { entry: FuturesTickerEntry & { streamerSymbol: string }; period: CandlePeriod }) {
  const hostRef = useRef<HTMLDivElement>(null);
  const [info, setInfo] = useState<ChartInfo>({ loaded: false, count: 0, last: null, updatedAt: null });
  const ws = useWsState();
  const symbol = entry.streamerSymbol;

  useEffect(() => {
    const el = hostRef.current;
    if (el === null) return;
    const precision = PRECISION[entry.product] ?? 2;
    const chart = createChart(el, {
      autoSize: true,
      layout: { background: { color: "transparent" }, textColor: "#a6adb8" },
      grid: { vertLines: { color: "#1a1d23" }, horzLines: { color: "#1a1d23" } },
      rightPriceScale: { borderColor: "#23262d" },
      timeScale: {
        borderColor: "#23262d",
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
      chart.remove();
    };
  }, [symbol, period, entry.product]);

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
      <div ref={hostRef} style={{ height: "560px" }} />
    </>
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

  const entries = data?.entries ?? [];
  const entry = entries.find((e) => e.product === product);
  const products = entries.map((e) => e.product);

  return (
    <section className="card">
      <div className="card-head">
        <h2>
          {entry?.label ?? `/${product}`} {entry?.contract !== null && entry?.contract !== undefined ? `(${entry.contract})` : ""} ·{" "}
          {period} candles
        </h2>
        <span className="chip">chart only</span>
        <div style={{ marginLeft: "auto", display: "flex", gap: 8 }}>
          {products.length > 0 && (
            <Toggle value={product} options={products} onChange={(v) => set("product", v)} label="contract" render={(p) => `/${p}`} />
          )}
          <Toggle value={period} options={CANDLE_PERIODS} onChange={(v) => set("period", v)} label="candle width" />
        </div>
      </div>
      {data === undefined ? (
        isError ? <p className="muted">Could not read the futures contract map from the console server.</p> : <p className="muted">loading…</p>
      ) : entry === undefined ? (
        <p className="muted">/{product} is not one of the ticker's contracts ({products.map((p) => `/${p}`).join(", ")}).</p>
      ) : entry.streamerSymbol === null ? (
        <p className="muted">No contract for /{product}: {entry.reason ?? "unknown"} (see scripts/refresh_futures_contracts.py).</p>
      ) : (
        <LiveCandles key={`${entry.streamerSymbol}|${period}`} entry={{ ...entry, streamerSymbol: entry.streamerSymbol }} period={period} />
      )}
      <p className="muted" style={{ fontSize: "0.85em", marginTop: 8 }}>
        Extended hours, times in ET. CME futures trade Sunday 18:00 to Friday 17:00 ET with a daily break from 17:00
        to 18:00; the axis skips periods with no bars, so the break and weekends close up rather than showing as
        gaps. The bar in progress updates about once a second while the contract trades.
      </p>
    </section>
  );
}
