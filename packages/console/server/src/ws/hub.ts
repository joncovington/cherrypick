import type { FastifyInstance } from "fastify";
import type { WebSocket } from "ws";
import type { CandleMessage, ClientMessage, ServerMessage, QuoteTick, WsStatus } from "@console/shared";
import type { MarketDataService } from "../market/marketData.js";
import { isCandlePeriod, isCandleSymbol, type CandleService } from "../market/candles.js";

const STATUS_INTERVAL_MS = 5_000;

interface SocketSubs {
  symbols: Set<string>;
  /** `symbol|period` for each live candle series this socket holds. */
  candles: Set<string>;
}

const candleKey = (symbol: string, period: string): string => `${symbol}|${period}`;

/**
 * One WS endpoint at /ws. Each socket declares the symbols it wants; the hub
 * refcounts them into the MarketDataService and fans ticks back out only to
 * sockets subscribed to that symbol. Candle series ride the same socket, refcounted into the
 * CandleService. A status heartbeat rides it too.
 */
export function registerWsHub(app: FastifyInstance, market: MarketDataService, candles: CandleService): void {
  const sockets = new Map<WebSocket, SocketSubs>();

  function send(ws: WebSocket, msg: ServerMessage): void {
    if (ws.readyState === ws.OPEN) ws.send(JSON.stringify(msg));
  }

  function statusMessage(): WsStatus {
    const dx = market.dxState;
    return {
      type: "status",
      marketData: dx === "connected" ? "live" : "cached",
      dxlink: dx,
      ts: Date.now(),
    };
  }

  market.on("tick", (tick: QuoteTick) => {
    for (const [ws, subs] of sockets) {
      if (subs.symbols.has(tick.symbol)) send(ws, tick);
    }
  });

  candles.on("candles", (msg: CandleMessage) => {
    const key = candleKey(msg.symbol, msg.period);
    for (const [ws, subs] of sockets) {
      if (subs.candles.has(key)) send(ws, msg);
    }
  });

  market.on("state", () => {
    for (const ws of sockets.keys()) send(ws, statusMessage());
  });

  const heartbeat = setInterval(() => {
    for (const ws of sockets.keys()) send(ws, statusMessage());
  }, STATUS_INTERVAL_MS);
  app.addHook("onClose", () => clearInterval(heartbeat));

  app.get("/ws", { websocket: true }, (socket: WebSocket) => {
    sockets.set(socket, { symbols: new Set(), candles: new Set() });
    send(socket, statusMessage());

    socket.on("message", (data: Buffer | string) => {
      let msg: ClientMessage;
      try {
        msg = JSON.parse(String(data)) as ClientMessage;
      } catch {
        return;
      }
      const subs = sockets.get(socket);
      if (subs === undefined || typeof msg !== "object" || msg === null) return;

      if (msg.op === "candles" || msg.op === "candlesOff") {
        if (!isCandleSymbol(msg.symbol) || !isCandlePeriod(msg.period)) return;
        const key = candleKey(msg.symbol, msg.period);
        if (msg.op === "candles") {
          if (subs.candles.has(key)) return;
          subs.candles.add(key);
          candles.acquire(msg.symbol, msg.period);
          const snap = candles.snapshot(msg.symbol, msg.period);
          if (snap !== null) send(socket, snap);
        } else if (subs.candles.delete(key)) {
          candles.release(msg.symbol, msg.period);
        }
        return;
      }

      if (!Array.isArray(msg.symbols)) return;
      if (msg.op === "subscribe") {
        for (const symbol of msg.symbols) {
          if (typeof symbol !== "string" || subs.symbols.has(symbol)) continue;
          subs.symbols.add(symbol);
          market.subscribe(symbol);
          const snap = market.snapshot(symbol);
          if (snap !== null) send(socket, snap);
        }
      } else if (msg.op === "unsubscribe") {
        for (const symbol of msg.symbols) {
          if (!subs.symbols.delete(symbol)) continue;
          market.unsubscribe(symbol);
        }
      }
    });

    socket.on("close", () => {
      const subs = sockets.get(socket);
      if (subs !== undefined) {
        for (const symbol of subs.symbols) market.unsubscribe(symbol);
        for (const key of subs.candles) {
          const [symbol, period] = key.split("|") as [string, string];
          if (isCandlePeriod(period)) candles.release(symbol, period);
        }
      }
      sockets.delete(socket);
    });
  });
}
