import type { FastifyInstance } from "fastify";
import type { ConsoleConfig } from "../config.js";
import {
  getWatchlist,
  addToWatchlist,
  removeFromWatchlist,
  importScoutWatchlist,
} from "../store/consoleDb.js";
import { readDailyCandles, candleSymbols } from "../readers/scoutdb.js";
import { readChain } from "../readers/chain.js";
import { movingAverages, supportResistance } from "../analytics/levels.js";
import { classifyTrend } from "../analytics/trend.js";

const SYMBOL_RE = /^[A-Z][A-Z0-9./]{0,9}$/;

export function registerScoutRoutes(app: FastifyInstance, config: ConsoleConfig): void {
  app.get("/api/watchlist", async () => ({ symbols: getWatchlist(config) }));

  app.post("/api/watchlist", async (req, reply) => {
    const body = req.body as { symbol?: unknown };
    const symbol = typeof body?.symbol === "string" ? body.symbol.trim().toUpperCase() : "";
    if (!SYMBOL_RE.test(symbol)) {
      return reply.code(400).send({ error: "invalid symbol" });
    }
    addToWatchlist(config, symbol);
    return { symbols: getWatchlist(config) };
  });

  app.delete("/api/watchlist/:symbol", async (req) => {
    const { symbol } = req.params as { symbol: string };
    removeFromWatchlist(config, symbol.toUpperCase());
    return { symbols: getWatchlist(config) };
  });

  app.post("/api/watchlist/import", async () => {
    const result = importScoutWatchlist(config);
    return { ...result, symbols: getWatchlist(config) };
  });

  app.get("/api/chain/:symbol", async (req) => {
    const { symbol } = req.params as { symbol: string };
    const { expiration } = req.query as { expiration?: string };
    return readChain(config, symbol.toUpperCase(), expiration ?? null);
  });

  app.get("/api/symbol/:symbol", async (req, reply) => {
    const { symbol } = req.params as { symbol: string };
    const sym = symbol.toUpperCase();
    const bars = readDailyCandles(config, sym);
    if (bars.length === 0) {
      return reply.code(404).send({
        error: "no cached candles for symbol",
        available: candleSymbols(config),
      });
    }
    const closes = bars.map((b) => b.c);
    return {
      symbol: sym,
      bars,
      overlays: movingAverages(bars),
      levels: supportResistance(bars),
      trend: classifyTrend(closes),
    };
  });
}
