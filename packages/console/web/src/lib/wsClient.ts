import type { ClientMessage, ServerMessage, QuoteTick, MarketDataState } from "@console/shared";

export interface QuoteState {
  bid?: number;
  ask?: number;
  last?: number;
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
    };
    ws.onmessage = (ev) => {
      let msg: ServerMessage;
      try {
        msg = JSON.parse(String(ev.data)) as ServerMessage;
      } catch {
        return;
      }
      if (msg.type === "tick") this.applyTick(msg);
      else if (msg.type === "status") {
        this.setState({ ...this.state, marketData: msg.marketData, dxlink: msg.dxlink });
      }
    };
    ws.onclose = () => {
      this.ws = null;
      this.setState({ ...this.state, socket: "closed", marketData: "cached", dxlink: "disconnected" });
      if (this.refs.size > 0) this.scheduleReconnect();
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
