import type { FastifyInstance } from "fastify";
import type { WebSocket } from "ws";
import type { ClientMessage, ServerMessage, QuoteTick, WsStatus } from "@console/shared";
import type { MarketDataService } from "../market/marketData.js";

const STATUS_INTERVAL_MS = 5_000;

/**
 * One WS endpoint at /ws. Each socket declares the symbols it wants; the hub
 * refcounts them into the MarketDataService and fans ticks back out only to
 * sockets subscribed to that symbol. A status heartbeat rides the same socket.
 */
export function registerWsHub(app: FastifyInstance, market: MarketDataService): void {
  const sockets = new Map<WebSocket, Set<string>>();

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
      if (subs.has(tick.symbol)) send(ws, tick);
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
    sockets.set(socket, new Set());
    send(socket, statusMessage());

    socket.on("message", (data: Buffer | string) => {
      let msg: ClientMessage;
      try {
        msg = JSON.parse(String(data)) as ClientMessage;
      } catch {
        return;
      }
      const subs = sockets.get(socket);
      if (subs === undefined || !Array.isArray(msg.symbols)) return;

      if (msg.op === "subscribe") {
        for (const symbol of msg.symbols) {
          if (typeof symbol !== "string" || subs.has(symbol)) continue;
          subs.add(symbol);
          market.subscribe(symbol);
          const snap = market.snapshot(symbol);
          if (snap !== null) send(socket, snap);
        }
      } else if (msg.op === "unsubscribe") {
        for (const symbol of msg.symbols) {
          if (!subs.delete(symbol)) continue;
          market.unsubscribe(symbol);
        }
      }
    });

    socket.on("close", () => {
      const subs = sockets.get(socket);
      if (subs !== undefined) {
        for (const symbol of subs) market.unsubscribe(symbol);
      }
      sockets.delete(socket);
    });
  });
}
