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
  TickMarkType,
  type Time,
  type UTCTimestamp,
  type WhitespaceData,
} from "lightweight-charts";
import { CANDLE_PERIODS, PROFILE_SYMBOLS, type CandleBar, type CandlePeriod, type FuturesTickerEntry } from "@console/shared";
import { useFuturesTicker, useProfileBars } from "../../lib/api";
import { overnightProfile, untestedPocs, type MarketComposite } from "../../lib/marketProfile";
import { useCandles } from "../../lib/useCandles";
import { CardHelpButton, HelpSection, type CardHelpContent } from "../../components/CardHelp";
import { useWsState } from "../../lib/useQuote";
import { wsClient } from "../../lib/wsClient";
import { canvasPalette as _pal, hexOf } from "../../components/chart/cssvars";
import { tokenAlpha } from "../../components/chart/cssvars";
import type { MarketProfile } from "../../lib/marketProfile";
import {
  dashFor,
  hiddenRoles,
  hideParam,
  LINE_LEGEND,
  lineStyleFor,
  OPTIONAL_LINES,
  type ProfileLine,
  ProfileHistogram,
  profileLines,
  sessionProfiles,
  untestedBars,
  untestedCandidates,
  overnightSession,
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

/** How many recorded sessions back the untested-POC lines look. */
const UNTESTED_SESSIONS = 20;

/** The profile-bars store's name for a chart product, or "" for one it does not record. */
export function recordedProfileSymbol(product: string): string {
  const name = isCash(product) ? product : `/${product}`;
  return (PROFILE_SYMBOLS as readonly string[]).includes(name) ? name : "";
}

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
const ET_DAY = new Intl.DateTimeFormat("en-US", { timeZone: ET, weekday: "short", month: "short", day: "numeric" });
const ET_WEEKDAY_DATE = new Intl.DateTimeFormat("en-US", { timeZone: ET, weekday: "short", day: "numeric" });
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
 * The time axis on the session's clock, wherever the viewer sits.
 *
 * The library marks a new day (`TickMarkType.DayOfMonth` or heavier) on the first bar of each UTC day:
 * a cash symbol's 09:30 open, or 19:00/20:00 ET on a future. That tick is where days part, so it
 * carries the ET weekday and date of its own instant beside its ET time ("Thu 13 09:30"); with only
 * the time, a week of cash sessions read as a row of bold "09:30"s and no day could be told apart.
 * Dating it with the UTC day instead put "Oct 1" at 20:00 on Oct 1 (the first browser check). ET
 * midnight, where a future has a bar, is the date alone ("Fri Nov 14"); every other tick is the time.
 */
const ET_DATE_ONLY = new Intl.DateTimeFormat("en-CA", { timeZone: ET, year: "numeric", month: "2-digit", day: "2-digit" });

/**
 * The span the chart shows, on the ET clock, for the status line: "Thu Nov 12 18:00 – Fri Nov 13
 * 16:55 ET", or "Fri Nov 13 05:30–16:55 ET" within one day. `from` and `to` are bar starts in epoch
 * seconds (the library's visible time range).
 */
export function visibleSpanLabel(from: number, to: number): string {
  const day = (t: number): string => ET_DAY.format(t * 1000).replace(",", "");
  const hm = (t: number): string => ET_TIME.format(t * 1000);
  if (ET_DATE_ONLY.format(from * 1000) === ET_DATE_ONLY.format(to * 1000)) return `${day(from)} ${hm(from)}–${hm(to)} ET`;
  return `${day(from)} ${hm(from)} – ${day(to)} ${hm(to)} ET`;
}

export function etTickMark(time: Time, tickMarkType: TickMarkType = TickMarkType.Time): string {
  const ms = (time as number) * 1000;
  const hm = ET_TIME.format(ms);
  if (hm === "00:00") return ET_DAY.format(ms).replace(",", "");
  if (tickMarkType <= TickMarkType.DayOfMonth) return `${ET_WEEKDAY_DATE.format(ms).replace(/^(\d+) (\w+)$/, "$2 $1")} ${hm}`;
  return hm;
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

function ProfileReadout({
  profiles,
  precision,
  untested,
  overnight,
}: {
  profiles: SessionProfiles;
  precision: number;
  untested: ReadonlyArray<readonly [string, number]> | null;
  overnight: MarketComposite | null;
}) {
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
      {untested !== null && (
        <span>
          {" "}
          · untested POCs:{" "}
          {untested.length === 0
            ? "none"
            : untested.map(([s, poc]) => `${f(poc)} (${sessionLabel(s)})`).join(", ")}
        </span>
      )}
      {overnight !== null && (
        <span>
          {" "}
          · overnight before {sessionLabel(overnight.sessions[0]!)}: H {f(overnight.high)} · POC {f(overnight.poc)} · L{" "}
          {f(overnight.low)}
        </span>
      )}
    </div>
  );
}

function LiveCandles({
  entry,
  period,
  profile,
  hidden,
  onToggleLine,
}: {
  entry: FuturesTickerEntry & { streamerSymbol: string };
  period: CandlePeriod;
  profile: boolean;
  hidden: ReadonlySet<ProfileLine["role"]>;
  onToggleLine: (role: ProfileLine["role"]) => void;
}) {
  const hostRef = useRef<HTMLDivElement>(null);
  const [info, setInfo] = useState<ChartInfo>({ loaded: false, count: 0, last: null, updatedAt: null });
  const [span, setSpan] = useState<{ from: number; to: number } | null>(null);
  const ws = useWsState();
  const symbol = entry.streamerSymbol;
  const seriesRef = useRef<ISeriesApi<"Candlestick", Time> | null>(null);
  const histRef = useRef<ProfileHistogram | null>(null);
  const linesRef = useRef<{ key: string; lines: IPriceLine[] }>({ key: "", lines: [] });

  // The profile reads the 15-minute series whatever width is drawn (the same series when it is 15m).
  const profileBars = useCandles(profile ? symbol : "", "15m");
  const storeName = recordedProfileSymbol(entry.product);
  const profiles = useMemo(() => sessionProfiles(profileBars, Date.now(), storeName), [profileBars, storeName]);

  // Untested POCs need more history than the live 15-minute series holds: the recorded bars, extended
  // hours, for the symbols the profile-bars job keeps. Off (no request) for any other contract, and
  // when the profile or its nPOC lines are switched off.
  const recordedSymbol = profile && !hidden.has("untested-poc") ? recordedProfileSymbol(entry.product) : "";
  const { data: recorded } = useProfileBars(recordedSymbol, UNTESTED_SESSIONS, "extended");
  const untested = useMemo((): Array<[string, number]> => {
    const rowSize = profiles.latest?.rowSize;
    if (recordedSymbol === "" || recorded === undefined || !recorded.available || rowSize === undefined) return [];
    return untestedPocs(untestedBars(recorded.bars, profileBars), {
      sessions: untestedCandidates(recorded.sessions, profiles),
      rowSize,
      barSeconds: recorded.barSeconds,
    });
  }, [recordedSymbol, recorded, profiles, profileBars]);

  // A future's overnight (18:00-09:30 ET) from the same live 15-minute series, which carries the
  // extended hours; a cash symbol has none worth the name.
  const isFuture = !isCash(entry.product);
  const overnight = useMemo((): MarketComposite | null => {
    const rowSize = profiles.latest?.rowSize;
    if (!isFuture || !profile || hidden.has("overnight") || rowSize === undefined) return null;
    const session = overnightSession(profileBars);
    return session === null ? null : overnightProfile(profileBars, { session, rowSize, barSeconds: 900 });
  }, [isFuture, profile, hidden, profiles, profileBars]);

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
        // A day-change label ("Thu 13 09:30") is wider than the default eight characters allow for.
        tickMarkMaxCharacterLength: 12,
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
    // The days in view, for the status line: a view with no day change in it has no date on its axis.
    const onRange = (r: { from: Time; to: Time } | null): void =>
      setSpan(r === null ? null : { from: r.from as number, to: r.to as number });
    chart.timeScale().subscribeVisibleTimeRangeChange(onRange);

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
      chart.timeScale().unsubscribeVisibleTimeRangeChange(onRange);
      chart.remove();
    };
  }, [symbol, period, entry.product]);

  // The overlay follows the profile; price lines are rebuilt only when a level actually moves (the
  // 15-minute bar in progress is re-sent about once a second).
  useEffect(() => {
    const series = seriesRef.current;
    histRef.current?.setProfile(profile ? profiles.latest : null);
    if (series === null) return;
    const want = profile ? profileLines(profiles, hidden, untested, overnight) : [];
    const key = want.map((l) => `${l.key}:${l.price}`).join("|");
    if (key === linesRef.current.key) return;
    for (const line of linesRef.current.lines) series.removePriceLine(line);
    const lines = want.map((l) => series.createPriceLine({ price: l.price, title: l.title, ...lineStyleFor(l.role, OVERLAY) }));
    linesRef.current = { key, lines };
  }, [profile, profiles, hidden, untested, overnight]);

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
            {span !== null && <span> · showing {visibleSpanLabel(span.from, span.to)}</span>}
          </>
        )}
        {!live && <span className="chip" style={{ marginLeft: 8 }}>feed {ws.dxlink} — not live</span>}
      </div>
      {profile && profileBars.length > 0 && <ProfileReadout profiles={profiles} precision={precision} untested={recordedSymbol === "" || recorded === undefined || !recorded.available ? null : untested}
          overnight={overnight}
        />}
      <div ref={hostRef} style={{ height: "560px" }} />
      {profile && profileBars.length > 0 && <ProfileLegend hidden={hidden} onToggle={onToggleLine} />}
    </>
  );
}

function Swatch({ role }: { role: ProfileLine["role"] }) {
  const s = lineStyleFor(role, OVERLAY);
  return (
    <svg width="24" height="8" aria-hidden="true" style={{ flex: "none" }}>
      <line x1="0" y1="4" x2="24" y2="4" stroke={s.color} strokeWidth="2" strokeDasharray={dashFor(s.lineStyle)} />
    </svg>
  );
}

const OPTIONAL = new Set(OPTIONAL_LINES.map((o) => o.role));

/**
 * The legend under the chart, which is also where lines are switched: POC and the value area are
 * always drawn and listed plainly; IBH/IBL, pPOC and pVAH/pVAL are buttons that show or hide their
 * lines (and so their price-axis labels). Each entry's meaning is its tooltip and in the help box.
 */
function ProfileLegend({ hidden, onToggle }: { hidden: ReadonlySet<ProfileLine["role"]>; onToggle: (role: ProfileLine["role"]) => void }) {
  return (
    <div style={{ display: "flex", flexWrap: "wrap", alignItems: "center", gap: "6px 14px", marginTop: 8, fontSize: "0.85em" }}>
      {LINE_LEGEND.filter((e) => !OPTIONAL.has(e.role)).map(({ role, label, meaning }) => (
        <span key={role} title={meaning} style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
          <Swatch role={role} />
          <strong>{label}</strong>
        </span>
      ))}
      <div className="mode-toggle" role="group" aria-label="optional profile lines">
        {LINE_LEGEND.filter((e) => OPTIONAL.has(e.role)).map(({ role, label, meaning }) => {
          const on = !hidden.has(role);
          return (
            <button
              key={role}
              type="button"
              className={on ? "mode-btn active" : "mode-btn"}
              aria-pressed={on}
              title={`${on ? "Hide" : "Show"}: ${meaning}`}
              onClick={() => onToggle(role)}
              style={{ display: "inline-flex", alignItems: "center", gap: 6, opacity: on ? 1 : 0.55 }}
            >
              <Swatch role={role} />
              {label}
            </button>
          );
        })}
      </div>
    </div>
  );
}

/** The page's help box: what the chart is, how the profile is built, and what each line means. */
const INTRADAY_HELP: CardHelpContent = {
  title: "Reading the intraday chart",
  body: (
    <>
      <HelpSection title="The chart">
        <p>
          Extended hours, times in ET. CME futures trade Sunday 18:00 to Friday 17:00 ET with a daily break from 17:00
          to 18:00; the axis skips periods with no bars, so the break and weekends close up rather than showing as
          gaps. The bar in progress updates about once a second while the market trades.
        </p>
      </HelpSection>
      <HelpSection title="Market Profile">
        <p>
          Built from 15-minute candles over the regular session, 09:30–16:00 ET, for every symbol (a future's
          overnight is left out), in 30-minute periods lettered A–M. The shaded histogram on the right is the newest
          session's time at each price: the brighter rows are its value area (70% of the time), the amber row its
          point of control. SPX and NDX carry no volume, so theirs is time only. Chart only: no module reads it.
        </p>
      </HelpSection>
      <HelpSection title="The lines">
        <ul>
          {LINE_LEGEND.map(({ role, label, meaning }) => (
            <li key={role}>
              <strong>{label}</strong>: {meaning}
              {OPTIONAL.has(role) ? " (switch it with its button under the chart)" : " (always drawn)"}.
            </li>
          ))}
          <li>
            The coloured line whose label is the price itself is the last trade.
          </li>
        </ul>
      </HelpSection>
    </>
  ),
};

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
  const hideRaw = params.get("hide");
  const hidden = useMemo(() => hiddenRoles(hideRaw), [hideRaw]);
  const toggleLine = (role: ProfileLine["role"]): void => {
    const nextHidden = new Set(hidden);
    if (nextHidden.has(role)) nextHidden.delete(role);
    else nextHidden.add(role);
    const next = new URLSearchParams(params);
    const value = hideParam(nextHidden);
    if (value === "") next.delete("hide");
    else next.set("hide", value);
    setParams(next, { replace: true });
  };

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
        <CardHelpButton cardTitle="Intraday chart" help={INTRADAY_HELP} />
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
          hidden={hidden}
          onToggleLine={toggleLine}
        />
      )}
    </section>
  );
}
