import { EventEmitter } from "node:events";
import { CANDLE_PERIODS, type CandleBar, type CandleMessage, type CandlePeriod } from "@console/shared";
import type { MarketDataService } from "./marketData.js";

/**
 * Live intraday candles on the console's own DXLink session, for the moving chart.
 *
 * Measured before it was built (`scripts/probe_candles.py`, /ES overnight, 2026-10-01), and each
 * finding is a rule here:
 *
 *  - **dxFeed renames a multiplier of 1.** `X{=1m}` arrives as `X{=m}`, so events are matched by the
 *    parsed period, never by the string subscribed (an exact match drops every 1-minute bar).
 *  - **History arrives as one snapshot, newest first,** bracketed by SNAPSHOT_BEGIN … SNAPSHOT_END.
 *    It is buffered and sent whole (`replace`); streaming it bar by bar would hand the chart bars
 *    older than the ones it already drew.
 *  - **The snapshot ends with a removal sentinel that carries no prices.** A bar without a full
 *    positive OHLC is never drawn; a REMOVE deletes the bar at its time.
 *  - **The bar in progress is re-sent with the same time;** a new time means the previous bar is
 *    final. A live event is therefore "replace the bar at `t`".
 *  - **Extended hours.** The symbol carries no `tho=true`, so the overnight session is included.
 *
 * Viewer-gated with a linger, like the quote path: a series is subscribed upstream only while a
 * browser holds it, and dropped 30s after the last one lets go. Chart history only; it informs no
 * decision anywhere in the suite.
 */

export type CandleFeed = Pick<MarketDataService, "on" | "addCandleSubscription" | "removeCandleSubscription">;

const LINGER_MS = 30_000;
/** How much history a series loads on subscribe: enough to fill a screen at each width. */
const LOOKBACK_MS: Record<CandlePeriod, number> = {
  "1m": 86_400_000,
  "5m": 3 * 86_400_000,
  "15m": 7 * 86_400_000,
};
/** A snapshot that never sends its end flag (or a symbol with no bars at all) is declared complete
 *  after this much silence, so a viewer is not left waiting on an empty chart forever. */
const SNAPSHOT_QUIET_MS = 5_000;
const MAX_BARS = 5_000;

const REMOVE_EVENT = 0x02;
const SNAPSHOT_BEGIN = 0x04;
const SNAPSHOT_END = 0x08;
const SNAPSHOT_SNIP = 0x10;

/** A DXLink streamer symbol as the browser may send it: no braces, so no candle attribute can be
 *  smuggled into the subscription the server builds. */
const SYMBOL_RE = /^[A-Za-z0-9./:_$^-]{1,48}$/;

export function isCandlePeriod(p: unknown): p is CandlePeriod {
  return typeof p === "string" && (CANDLE_PERIODS as readonly string[]).includes(p);
}

export function isCandleSymbol(s: unknown): s is string {
  return typeof s === "string" && SYMBOL_RE.test(s);
}

export function candleSymbol(symbol: string, period: CandlePeriod): string {
  return `${symbol}{=${period}}`;
}

/**
 * The series an event belongs to, from the symbol the FEED labelled it with: `/ESZ26:XCME{=m}` is
 * the 1-minute series. Anything that is not exactly one period attribute we offer is not ours.
 */
export function parseCandleSymbol(label: string): { symbol: string; period: CandlePeriod } | null {
  const m = /^(.+)\{=(\d*)([a-z]+)\}$/.exec(label);
  if (m === null) return null;
  const period = `${m[2] === "" ? 1 : Number(m[2])}${m[3]}`;
  return isCandlePeriod(period) ? { symbol: m[1]!, period } : null;
}

function num(e: Record<string, unknown>, k: string): number | null {
  const v = e[k];
  return typeof v === "number" && Number.isFinite(v) ? v : null;
}

/** A drawable bar, or null: every price must be present and positive (the sentinel has none). */
function toBar(e: Record<string, unknown>, t: number): CandleBar | null {
  const o = num(e, "open");
  const h = num(e, "high");
  const l = num(e, "low");
  const c = num(e, "close");
  if (o === null || h === null || l === null || c === null || o <= 0 || h <= 0 || l <= 0 || c <= 0) return null;
  return { t, o, h, l, c, v: num(e, "volume") ?? 0 };
}

interface Series {
  symbol: string;
  period: CandlePeriod;
  refs: number;
  lingerTimer: NodeJS.Timeout | null;
  /** Subscribed on the current feed. */
  upstream: boolean;
  bars: Map<number, CandleBar>;
  /** The snapshot has ended; events after this are live. */
  ready: boolean;
  quietTimer: NodeJS.Timeout | null;
  pending: Map<number, CandleBar>;
  replacePending: boolean;
}

const seriesKey = (symbol: string, period: CandlePeriod): string => `${symbol}|${period}`;

/** Emits "candles" (CandleMessage). */
export class CandleService extends EventEmitter {
  private series = new Map<string, Series>();
  private flushScheduled = false;

  constructor(private readonly feed: CandleFeed) {
    super();
    feed.on("feed", (e: Record<string, unknown>) => this.onEvent(e));
    feed.on("state", (state: string) => this.onState(state));
  }

  acquire(symbol: string, period: CandlePeriod): void {
    const key = seriesKey(symbol, period);
    let s = this.series.get(key);
    if (s === undefined) {
      s = {
        symbol,
        period,
        refs: 0,
        lingerTimer: null,
        upstream: false,
        bars: new Map(),
        ready: false,
        quietTimer: null,
        pending: new Map(),
        replacePending: false,
      };
      this.series.set(key, s);
    }
    s.refs += 1;
    if (s.lingerTimer !== null) {
      clearTimeout(s.lingerTimer);
      s.lingerTimer = null;
    }
    if (!s.upstream) void this.subscribeUpstream(s);
  }

  release(symbol: string, period: CandlePeriod): void {
    const key = seriesKey(symbol, period);
    const s = this.series.get(key);
    if (s === undefined) return;
    s.refs = Math.max(0, s.refs - 1);
    if (s.refs > 0 || s.lingerTimer !== null) return;
    s.lingerTimer = setTimeout(() => {
      s.lingerTimer = null;
      if (s.refs > 0) return;
      if (s.quietTimer !== null) clearTimeout(s.quietTimer);
      if (s.upstream) this.feed.removeCandleSubscription(candleSymbol(s.symbol, s.period));
      this.series.delete(key);
    }, LINGER_MS);
  }

  /** The whole series for a viewer joining one already loaded; null while its snapshot is still
   *  arriving (the viewer gets the `replace` with everyone else when it ends). */
  snapshot(symbol: string, period: CandlePeriod): CandleMessage | null {
    const s = this.series.get(seriesKey(symbol, period));
    return s !== undefined && s.ready ? this.whole(s) : null;
  }

  private whole(s: Series): CandleMessage {
    const bars = [...s.bars.values()].sort((a, b) => a.t - b.t);
    return { type: "candles", symbol: s.symbol, period: s.period, replace: true, bars };
  }

  private async subscribeUpstream(s: Series): Promise<void> {
    s.upstream = true;
    // After a reconnect, ask again from the newest bar held: the new snapshot fills the gap and is
    // merged into what the series already has.
    const newest = s.bars.size > 0 ? Math.max(...s.bars.keys()) * 1000 : null;
    const fromTime = newest ?? Date.now() - LOOKBACK_MS[s.period];
    s.ready = false;
    this.armQuiet(s);
    try {
      await this.feed.addCandleSubscription(candleSymbol(s.symbol, s.period), fromTime);
    } catch {
      s.upstream = false; // retried when the feed next reports "connected"
    }
  }

  private onState(state: string): void {
    if (state === "connected") {
      for (const s of this.series.values()) if (!s.upstream && s.refs > 0) void this.subscribeUpstream(s);
    } else if (state === "disconnected" || state === "error") {
      // A rebuilt feed starts with no subscriptions.
      for (const s of this.series.values()) s.upstream = false;
    }
  }

  private onEvent(e: Record<string, unknown>): void {
    if (e["eventType"] !== "Candle" || typeof e["eventSymbol"] !== "string") return;
    const parsed = parseCandleSymbol(e["eventSymbol"]);
    if (parsed === null) return;
    const s = this.series.get(seriesKey(parsed.symbol, parsed.period));
    if (s === undefined) return;

    const flags = num(e, "eventFlags") ?? 0;
    // A re-snapshot (the feed's channel reopened and resubscribed) is buffered like the first.
    if (flags & SNAPSHOT_BEGIN && s.ready) s.ready = false;

    const time = num(e, "time");
    if (time !== null) {
      const t = Math.floor(time / 1000);
      if (flags & REMOVE_EVENT) {
        if (s.bars.delete(t) && s.ready) s.replacePending = true;
      } else {
        const bar = toBar(e, t);
        if (bar !== null) {
          s.bars.set(t, bar);
          if (s.ready) s.pending.set(t, bar);
        }
      }
    }

    if (!s.ready) {
      if (flags & (SNAPSHOT_END | SNAPSHOT_SNIP)) this.markReady(s);
      else this.armQuiet(s);
      return;
    }
    this.scheduleFlush();
  }

  private armQuiet(s: Series): void {
    if (s.quietTimer !== null) clearTimeout(s.quietTimer);
    s.quietTimer = setTimeout(() => {
      s.quietTimer = null;
      if (!s.ready) this.markReady(s);
    }, SNAPSHOT_QUIET_MS);
  }

  private markReady(s: Series): void {
    if (s.quietTimer !== null) {
      clearTimeout(s.quietTimer);
      s.quietTimer = null;
    }
    this.trim(s);
    s.ready = true;
    s.pending.clear();
    s.replacePending = false;
    this.emit("candles", this.whole(s));
  }

  private trim(s: Series): void {
    if (s.bars.size <= MAX_BARS) return;
    const times = [...s.bars.keys()].sort((a, b) => a - b);
    for (const t of times.slice(0, times.length - MAX_BARS)) s.bars.delete(t);
  }

  /** Live events arrive in batches; one message per series per batch. */
  private scheduleFlush(): void {
    if (this.flushScheduled) return;
    this.flushScheduled = true;
    setImmediate(() => {
      this.flushScheduled = false;
      for (const s of this.series.values()) {
        if (!s.ready) continue;
        if (s.replacePending) {
          s.replacePending = false;
          s.pending.clear();
          this.emit("candles", this.whole(s));
        } else if (s.pending.size > 0) {
          const bars = [...s.pending.values()].sort((a, b) => a.t - b.t);
          s.pending.clear();
          if (s.bars.size > MAX_BARS + 500) this.trim(s);
          this.emit("candles", { type: "candles", symbol: s.symbol, period: s.period, replace: false, bars });
        }
      }
    });
  }
}
