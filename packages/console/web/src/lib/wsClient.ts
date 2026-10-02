import type {
  CandleBar,
  CandleMessage,
  CandlePeriod,
  ClientMessage,
  ServerMessage,
  QuoteTick,
  MarketDataState,
} from "@console/shared";

export interface QuoteState {
  bid?: number;
  ask?: number;
  last?: number;
  /** The prior session's close (a future's settle), from the feed's Summary event. */
  prevClose?: number;
  source: "dxlink" | "cache";
  /** Direction of the most recent last/mid change, for tick flashes. */
  direction: "up" | "down" | null;
  ts: number;
}

export interface WsState {
  marketData: MarketDataState;
  dxlink: "disconnected" | "connecting" | "connected" | "error";
  socket: "open" | "connecting" | "closed";
}

type Listener = () => void;
type CandleListener = (msg: CandleMessage) => void;

const candleKey = (symbol: string, period: CandlePeriod): string => `${symbol}|${period}`;

/**
 * Singleton reconnecting WebSocket client with client-side refcounting:
 * components take/release symbols via acquire/release, and the server is
 * only told about the first take and the last release.
 */
class WsClient {
  private ws: WebSocket | null = null;
  private refs = new Map<string, number>();
  private quotes = new Map<string, QuoteState>();
  private state: WsState = { marketData: "cached", dxlink: "disconnected", socket: "closed" };
  private quoteListeners = new Map<string, Set<Listener>>();
  private stateListeners = new Set<Listener>();
  private backoff = 1_000;
  private reconnectTimer: number | null = null;
  /** Candle series, refcounted like quotes: `symbol|period` → holders, bars and listeners. */
  private candleRefs = new Map<string, { symbol: string; period: CandlePeriod; n: number }>();
  private candleBars = new Map<string, Map<number, CandleBar>>();
  private candleListeners = new Map<string, Set<CandleListener>>();

  private connect(): void {
    if (this.ws !== null || this.reconnectTimer !== null) return;
    const proto = location.protocol === "https:" ? "wss" : "ws";
    const ws = new WebSocket(`${proto}://${location.host}/ws`);
    this.ws = ws;
    this.setState({ ...this.state, socket: "connecting" });

    ws.onopen = () => {
      this.backoff = 1_000;
      this.setState({ ...this.state, socket: "open" });
      const symbols = [...this.refs.keys()];
      if (symbols.length > 0) this.send({ op: "subscribe", symbols });
      for (const { symbol, period } of this.candleRefs.values()) this.send({ op: "candles", symbol, period });
    };
    ws.onmessage = (ev) => {
      let msg: ServerMessage;
      try {
        msg = JSON.parse(String(ev.data)) as ServerMessage;
      } catch {
        return;
      }
      if (msg.type === "tick") this.applyTick(msg);
      else if (msg.type === "candles") this.applyCandles(msg);
      else if (msg.type === "status") {
        this.setState({ ...this.state, marketData: msg.marketData, dxlink: msg.dxlink });
      }
    };
    ws.onclose = () => {
      this.ws = null;
      this.setState({ ...this.state, socket: "closed", marketData: "cached", dxlink: "disconnected" });
      if (this.refs.size > 0 || this.candleRefs.size > 0) this.scheduleReconnect();
    };
    ws.onerror = () => ws.close();
  }

  private scheduleReconnect(): void {
    if (this.reconnectTimer !== null) return;
    this.reconnectTimer = window.setTimeout(() => {
      this.reconnectTimer = null;
      this.connect();
    }, this.backoff);
    this.backoff = Math.min(this.backoff * 2, 15_000);
  }

  private send(msg: ClientMessage): void {
    if (this.ws?.readyState === WebSocket.OPEN) this.ws.send(JSON.stringify(msg));
  }

  private applyTick(tick: QuoteTick): void {
    const prev = this.quotes.get(tick.symbol);
    const prevVal = prev?.last ?? (prev?.bid !== undefined && prev?.ask !== undefined ? (prev.bid + prev.ask) / 2 : undefined);
    const next: QuoteState = {
      bid: tick.bid ?? prev?.bid,
      ask: tick.ask ?? prev?.ask,
      last: tick.last ?? prev?.last,
      prevClose: tick.prevClose ?? prev?.prevClose,
      source: tick.source,
      direction: prev?.direction ?? null,
      ts: tick.ts,
    };
    const nextVal = next.last ?? (next.bid !== undefined && next.ask !== undefined ? (next.bid + next.ask) / 2 : undefined);
    if (prevVal !== undefined && nextVal !== undefined && nextVal !== prevVal) {
      next.direction = nextVal > prevVal ? "up" : "down";
    }
    this.quotes.set(tick.symbol, next);
    for (const l of this.quoteListeners.get(tick.symbol) ?? []) l();
  }

  private applyCandles(msg: CandleMessage): void {
    const key = candleKey(msg.symbol, msg.period);
    if (!this.candleRefs.has(key)) return;
    let bars = this.candleBars.get(key);
    if (bars === undefined || msg.replace) {
      bars = new Map();
      this.candleBars.set(key, bars);
    }
    for (const b of msg.bars) bars.set(b.t, b);
    for (const l of this.candleListeners.get(key) ?? []) l(msg);
  }

  private setState(s: WsState): void {
    this.state = s;
    for (const l of this.stateListeners) l();
  }

  // ---- public API (used by hooks) ----

  acquire(symbol: string): void {
    const n = this.refs.get(symbol) ?? 0;
    this.refs.set(symbol, n + 1);
    if (n === 0) {
      this.connect();
      this.send({ op: "subscribe", symbols: [symbol] });
    }
  }

  release(symbol: string): void {
    const n = this.refs.get(symbol) ?? 0;
    if (n <= 1) {
      this.refs.delete(symbol);
      this.send({ op: "unsubscribe", symbols: [symbol] });
    } else {
      this.refs.set(symbol, n - 1);
    }
  }

  acquireCandles(symbol: string, period: CandlePeriod): void {
    const key = candleKey(symbol, period);
    const cur = this.candleRefs.get(key);
    if (cur !== undefined) {
      cur.n += 1;
      return;
    }
    this.candleRefs.set(key, { symbol, period, n: 1 });
    this.connect();
    this.send({ op: "candles", symbol, period });
  }

  releaseCandles(symbol: string, period: CandlePeriod): void {
    const key = candleKey(symbol, period);
    const cur = this.candleRefs.get(key);
    if (cur === undefined) return;
    if (cur.n > 1) {
      cur.n -= 1;
      return;
    }
    this.candleRefs.delete(key);
    this.candleBars.delete(key);
    this.send({ op: "candlesOff", symbol, period });
  }

  /** Every bar held for a series, oldest first. */
  getCandles(symbol: string, period: CandlePeriod): CandleBar[] {
    return [...(this.candleBars.get(candleKey(symbol, period))?.values() ?? [])].sort((a, b) => a.t - b.t);
  }

  candleCount(symbol: string, period: CandlePeriod): number {
    return this.candleBars.get(candleKey(symbol, period))?.size ?? 0;
  }

  onCandles(symbol: string, period: CandlePeriod, l: CandleListener): () => void {
    const key = candleKey(symbol, period);
    let set = this.candleListeners.get(key);
    if (set === undefined) {
      set = new Set();
      this.candleListeners.set(key, set);
    }
    set.add(l);
    return () => {
      set.delete(l);
    };
  }

  getQuote(symbol: string): QuoteState | undefined {
    return this.quotes.get(symbol);
  }

  getState(): WsState {
    return this.state;
  }

  onQuote(symbol: string, l: Listener): () => void {
    let set = this.quoteListeners.get(symbol);
    if (set === undefined) {
      set = new Set();
      this.quoteListeners.set(symbol, set);
    }
    set.add(l);
    return () => {
      set.delete(l);
    };
  }

  onState(l: Listener): () => void {
    this.stateListeners.add(l);
    this.connect();
    return () => {
      this.stateListeners.delete(l);
    };
  }
}

export const wsClient = new WsClient();
