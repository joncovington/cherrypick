import { EventEmitter } from "node:events";
import type { QuoteTick } from "@console/shared";
import type { ConsoleConfig } from "../config.js";
import { getClient, hasCredential } from "./session.js";
import { cachedQuote } from "../readers/streamcache.js";

export type DxState = "disconnected" | "connecting" | "connected" | "error";

const LINGER_MS = 30_000;
const HEARTBEAT_INTERVAL_MS = 30_000;
const HEARTBEAT_SILENCE_MS = 60_000;
/** Always-on sentinel: SPX ticks continuously during RTH, so feed silence
 *  longer than HEARTBEAT_SILENCE_MS means the session died, not a quiet tape. */
const SENTINEL_SYMBOL = "SPX";
const TICK_MEMORY_MAX = 5_000;

interface SymbolEntry {
  refs: number;
  lingerTimer: NodeJS.Timeout | null;
  subscribed: boolean;
}

/**
 * The console's own DXLink session, client-gated like scout's SSE poller:
 * a symbol is subscribed upstream only while at least one browser wants it,
 * and unsubscribed after a short linger once the last viewer detaches.
 * Emits "tick" (QuoteTick) and "state" (DxState).
 */
export class MarketDataService extends EventEmitter {
  private state: DxState = "disconnected";
  private symbols = new Map<string, SymbolEntry>();
  private connecting: Promise<void> | null = null;
  private detachFeedListener: (() => void) | null = null;
  private lastFeedEventAt = 0;
  private reconnecting = false;
  private ticks = new Map<string, { bid?: number; ask?: number; last?: number; ts: number }>();

  constructor(private readonly config: ConsoleConfig) {
    super();
  }

  get dxState(): DxState {
    return this.state;
  }

  private setState(s: DxState): void {
    if (this.state === s) return;
    this.state = s;
    this.emit("state", s);
  }

  /** Snapshot for instant paint: last cached values from the Python streamer. */
  snapshot(symbol: string): QuoteTick | null {
    const q = cachedQuote(this.config, symbol);
    if (q === null) return null;
    return { type: "tick", symbol, ...q, source: "cache", ts: Date.now() };
  }

  /** Last tick this process saw for a symbol, if newer than maxAgeS — the
   *  console's own quote memory, fed by every subscription and sweep. */
  recent(symbol: string, maxAgeS: number): { bid?: number; ask?: number; last?: number } | null {
    const t = this.ticks.get(symbol);
    if (t === undefined || Date.now() - t.ts > maxAgeS * 1000) return null;
    const { ts: _ts, ...q } = t;
    return q;
  }

  subscribe(symbol: string): void {
    let entry = this.symbols.get(symbol);
    if (entry === undefined) {
      entry = { refs: 0, lingerTimer: null, subscribed: false };
      this.symbols.set(symbol, entry);
    }
    entry.refs += 1;
    if (entry.lingerTimer !== null) {
      clearTimeout(entry.lingerTimer);
      entry.lingerTimer = null;
    }
    void this.ensureUpstream(symbol, entry);
  }

  unsubscribe(symbol: string): void {
    const entry = this.symbols.get(symbol);
    if (entry === undefined) return;
    entry.refs = Math.max(0, entry.refs - 1);
    if (entry.refs === 0 && entry.lingerTimer === null) {
      entry.lingerTimer = setTimeout(() => {
        entry.lingerTimer = null;
        if (entry.refs === 0 && entry.subscribed) {
          entry.subscribed = false;
          try {
            getClient().quoteStreamer.unsubscribe([symbol]);
          } catch {
            /* connection already gone */
          }
        }
      }, LINGER_MS);
    }
  }

  private async ensureUpstream(symbol: string, entry: SymbolEntry): Promise<void> {
    if (!hasCredential()) return;
    try {
      await this.ensureConnected();
    } catch {
      return; // state already set to error; cache fallback still serves
    }
    if (!entry.subscribed && entry.refs > 0) {
      entry.subscribed = true;
      try {
        getClient().quoteStreamer.subscribe([symbol]);
      } catch (err) {
        entry.subscribed = false;
        this.noteFeedError(err);
        // A stale-connection failure resets state above; one reconnect+retry
        // (connecting is shared, so concurrent callers pile onto one attempt).
        if (this.state === "disconnected" && entry.refs > 0) {
          try {
            await this.ensureConnected();
            entry.subscribed = true;
            getClient().quoteStreamer.subscribe([symbol]);
          } catch {
            entry.subscribed = false;
          }
        }
      }
    }
  }

  /**
   * The SDK throws "not connected" when its websocket dropped underneath us
   * while our state machine still says connected. Reset so the next
   * ensureConnected rebuilds the session instead of trusting stale state.
   */
  private noteFeedError(err: unknown): void {
    if (!/not connected/i.test((err as Error)?.message ?? "")) return;
    this.teardownFeed();
  }

  private teardownFeed(): void {
    this.detachFeedListener?.();
    this.detachFeedListener = null;
    this.connecting = null;
    for (const entry of this.symbols.values()) entry.subscribed = false;
    this.setState("disconnected");
  }

  /**
   * Full session rebuild for bulk jobs whose feed went quiet WITHOUT throwing
   * (delivery just stops; the SDK's state never flips). Discards the cached
   * client — a fresh websocket, not a re-connect on the dead one — and
   * resubscribes everything a viewer still holds.
   */
  async reconnectFeed(): Promise<void> {
    this.teardownFeed();
    const { resetClient } = await import("./session.js");
    resetClient();
    await this.ensureConnected();
    for (const [symbol, entry] of this.symbols) {
      if (entry.refs > 0) void this.ensureUpstream(symbol, entry);
    }
  }

  /** Run a streamer call; on a stale-connection error reconnect once and retry. */
  private async withFeed<T>(fn: () => T): Promise<T> {
    await this.ensureConnected();
    try {
      return fn();
    } catch (err) {
      this.noteFeedError(err);
      if (this.state !== "disconnected") throw err;
      await this.ensureConnected();
      for (const [symbol, entry] of this.symbols) {
        if (entry.refs > 0) void this.ensureUpstream(symbol, entry);
      }
      return fn();
    }
  }

  private ensureConnected(): Promise<void> {
    if (this.state === "connected") return Promise.resolve();
    if (this.connecting !== null) return this.connecting;
    this.setState("connecting");
    this.connecting = (async () => {
      const streamer = getClient().quoteStreamer;
      await streamer.connect();
      this.detachFeedListener?.();
      this.detachFeedListener = streamer.addEventListener((events: unknown) => {
        this.handleEvents(events);
      });
      try {
        streamer.subscribe([SENTINEL_SYMBOL]);
      } catch {
        /* heartbeat degrades to no-op without the sentinel */
      }
      this.lastFeedEventAt = Date.now();
      this.setState("connected");
    })();
    return this.connecting.catch((err: unknown) => {
      this.connecting = null;
      this.setState("error");
      throw err;
    });
  }

  /**
   * Bounded quote snapshot for a symbol batch — the screener's quote source
   * (there is no REST quote endpoint in the JS SDK). Subscribes, collects
   * conflated ticks until every symbol has a bid+ask or the timeout lapses,
   * then unsubscribes via the normal refcount path. Mirrors scout's
   * opened-on-demand, never-resident DXLink exception.
   */
  async snapshotQuotes(symbols: string[], timeoutMs = 5_000): Promise<Map<string, { bid?: number; ask?: number; last?: number }>> {
    const out = new Map<string, { bid?: number; ask?: number; last?: number }>();
    if (symbols.length === 0) return out;
    const pending = new Set(symbols);
    const listener = (tick: QuoteTick): void => {
      if (!pending.has(tick.symbol) && !out.has(tick.symbol)) return;
      const cur = out.get(tick.symbol) ?? {};
      if (tick.bid !== undefined) cur.bid = tick.bid;
      if (tick.ask !== undefined) cur.ask = tick.ask;
      if (tick.last !== undefined) cur.last = tick.last;
      out.set(tick.symbol, cur);
      if (cur.bid !== undefined && cur.ask !== undefined) pending.delete(tick.symbol);
    };
    this.on("tick", listener);
    for (const s of symbols) this.subscribe(s);
    try {
      const start = Date.now();
      while (pending.size > 0 && Date.now() - start < timeoutMs) {
        await new Promise((r) => setTimeout(r, 150));
      }
    } finally {
      this.off("tick", listener);
      for (const s of symbols) this.unsubscribe(s);
    }
    return out;
  }

  /**
   * Bounded quote+greeks snapshot for option symbols — the EOD chain
   * snapshot's data source. quoteStreamer.subscribe already covers Greeks
   * events, so this listens on the raw feed for Quote (bid/ask) and Greeks
   * (volatility/delta) per symbol until both arrived or the timeout lapses.
   */
  async snapshotOptionData(
    symbols: string[],
    timeoutMs = 8_000,
  ): Promise<Map<string, { bid?: number; ask?: number; iv?: number; delta?: number }>> {
    const out = new Map<string, { bid?: number; ask?: number; iv?: number; delta?: number }>();
    if (symbols.length === 0) return out;
    const wanted = new Set(symbols);
    const pending = new Set(symbols);
    const listener = (e: Record<string, unknown>): void => {
      const symbol = typeof e["eventSymbol"] === "string" ? e["eventSymbol"] : null;
      if (symbol === null || !wanted.has(symbol)) return;
      const n = (k: string): number | undefined => {
        const v = e[k];
        return typeof v === "number" && Number.isFinite(v) && v !== 0x7fffffff ? v : undefined;
      };
      const cur = out.get(symbol) ?? {};
      const bid = n("bidPrice");
      const ask = n("askPrice");
      const iv = n("volatility");
      const delta = n("delta");
      if (bid !== undefined) cur.bid = bid;
      if (ask !== undefined) cur.ask = ask;
      if (iv !== undefined) cur.iv = iv;
      if (delta !== undefined) cur.delta = delta;
      out.set(symbol, cur);
      if (cur.bid !== undefined && cur.ask !== undefined && cur.iv !== undefined) pending.delete(symbol);
    };
    this.on("feed", listener);
    for (const s of symbols) this.subscribe(s);
    try {
      const start = Date.now();
      while (pending.size > 0 && Date.now() - start < timeoutMs) {
        await new Promise((r) => setTimeout(r, 150));
      }
    } finally {
      this.off("feed", listener);
      for (const s of symbols) this.unsubscribe(s);
    }
    return out;
  }

  /**
   * Bounded daily-candle backfill via DXLink candle subscription — the
   * console's own chart-history source when scout's cache lacks a symbol.
   * Chart history only; never informs a decision. Collects until the feed
   * goes quiet or the hard cap lapses, then unsubscribes.
   */
  async backfillDailyCandles(
    symbol: string,
    days = 365,
    quietMs = 2_500,
    capMs = 25_000,
  ): Promise<Array<{ t: number; o: number; h: number; l: number; c: number; v: number }>> {
    await this.ensureConnected();
    const candleSymbol = `${symbol}{=d}`;
    const bars = new Map<number, { t: number; o: number; h: number; l: number; c: number; v: number }>();
    let lastEventAt = Date.now();
    const listener = (e: Record<string, unknown>): void => {
      if (e["eventSymbol"] !== candleSymbol) return;
      const n = (k: string): number | null => {
        const v = e[k];
        return typeof v === "number" && Number.isFinite(v) ? v : null;
      };
      const time = n("time");
      const o = n("open");
      const h = n("high");
      const l = n("low");
      const c = n("close");
      // A real value can still be zero/degenerate — a zero-filled placeholder
      // for the still-forming bar must not pass as genuine data (scout's rule).
      if (time === null || o === null || h === null || l === null || c === null || c <= 0 || o <= 0) return;
      lastEventAt = Date.now();
      bars.set(Math.floor(time / 1000), { t: Math.floor(time / 1000), o, h, l, c, v: n("volume") ?? 0 });
    };
    this.on("feed", listener);
    const { CandleType } = await import("@tastytrade/api");
    const fromTime = Date.now() - days * 86_400_000;
    try {
      await this.withFeed(() => getClient().quoteStreamer.subscribeCandles(symbol, fromTime, 1, CandleType.Day));
      const start = Date.now();
      while (Date.now() - start < capMs && (bars.size === 0 || Date.now() - lastEventAt < quietMs)) {
        await new Promise((r) => setTimeout(r, 250));
      }
    } finally {
      this.off("feed", listener);
      try {
        getClient().quoteStreamer.unsubscribe([candleSymbol]);
      } catch {
        /* candle unsubscribe is best-effort */
      }
    }
    return [...bars.values()].sort((a, b) => a.t - b.t);
  }

  /** dxfeed delivers event objects (sometimes batched in arrays); map defensively. */
  private handleEvents(events: unknown): void {
    const list = Array.isArray(events) ? events : [events];
    for (const raw of list.flat()) {
      if (typeof raw !== "object" || raw === null) continue;
      const e = raw as Record<string, unknown>;
      this.emit("feed", e);
      const symbol = typeof e["eventSymbol"] === "string" ? e["eventSymbol"] : null;
      if (symbol === null) continue;
      const tick: QuoteTick = { type: "tick", symbol, source: "dxlink", ts: Date.now() };
      let has = false;
      const numField = (k: string): number | undefined => {
        const v = e[k];
        return typeof v === "number" && Number.isFinite(v) && v !== 0x7fffffff ? v : undefined;
      };
      const bid = numField("bidPrice");
      const ask = numField("askPrice");
      const last = numField("price");
      const vol = numField("dayVolume");
      if (bid !== undefined) { tick.bid = bid; has = true; }
      if (ask !== undefined) { tick.ask = ask; has = true; }
      if (last !== undefined) { tick.last = last; has = true; }
      if (vol !== undefined) { tick.dayVolume = vol; has = true; }
      if (has) {
        this.lastFeedEventAt = tick.ts;
        // Bounded merge-memory; a full reset is fine — it refills from flow.
        if (this.ticks.size >= TICK_MEMORY_MAX && !this.ticks.has(symbol)) this.ticks.clear();
        const cur = this.ticks.get(symbol) ?? { ts: tick.ts };
        if (bid !== undefined) cur.bid = bid;
        if (ask !== undefined) cur.ask = ask;
        if (last !== undefined) cur.last = last;
        cur.ts = tick.ts;
        this.ticks.set(symbol, cur);
        this.emit("tick", tick);
      }
    }
  }

  /**
   * Silent-death watchdog: during RTH the SPX sentinel guarantees a steady
   * event flow, so a connected session with a minute of silence is dead —
   * the SDK never flips its own state when the websocket drops. Rebuilds the
   * session (fresh client) through the same path the bulk jobs use.
   */
  startHeartbeat(log: (msg: string) => void): NodeJS.Timeout {
    const timer = setInterval(() => {
      if (this.state !== "connected" || this.reconnecting) return;
      const et = new Intl.DateTimeFormat("en-US", {
        timeZone: "America/New_York",
        hour: "2-digit",
        minute: "2-digit",
        weekday: "short",
        hour12: false,
      }).formatToParts(new Date());
      const get = (t: string): string => et.find((p) => p.type === t)?.value ?? "";
      const minutes = (Number(get("hour")) % 24) * 60 + Number(get("minute"));
      if (["Sat", "Sun"].includes(get("weekday")) || minutes < 9 * 60 + 30 || minutes >= 16 * 60) return;
      if (Date.now() - this.lastFeedEventAt < HEARTBEAT_SILENCE_MS) return;
      log(`dxlink heartbeat: no events for ${Math.round((Date.now() - this.lastFeedEventAt) / 1000)}s — rebuilding session`);
      this.reconnecting = true;
      void this.reconnectFeed()
        .catch((err: unknown) => log(`dxlink heartbeat reconnect failed: ${(err as Error).message}`))
        .finally(() => {
          this.reconnecting = false;
        });
    }, HEARTBEAT_INTERVAL_MS);
    timer.unref();
    return timer;
  }
}
