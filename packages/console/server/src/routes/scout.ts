import type { FastifyInstance } from "fastify";
import type { ConsoleConfig } from "../config.js";
import {
  getWatchlist,
  addToWatchlist,
  removeFromWatchlist,
  importScoutWatchlist,
} from "../store/consoleDb.js";
import { candleSymbols } from "../readers/scoutdb.js";
import { readChain } from "../readers/chain.js";
import { getDailyBars } from "../services/candles.js";
import type { MarketDataService } from "../market/marketData.js";
import { movingAverages, supportResistance } from "../analytics/levels.js";
import { classifyTrend } from "../analytics/trend.js";
import { buildRows } from "../services/ttWatchlists.js";
import { buildSymbolAnalysis, buildEventWarnings } from "../services/symbolAnalysis.js";

const SYMBOL_RE = /^[A-Z][A-Z0-9./]{0,9}$/;

export function registerScoutRoutes(
  app: FastifyInstance,
  config: ConsoleConfig,
  market: MarketDataService,
): void {
  // Same enriched rows as the tastytrade tabs (metrics + candle EOD context).
  app.get("/api/watchlist", async () => {
    const symbols = getWatchlist(config);
    return { symbols, rows: await buildRows(config, symbols) };
  });

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

  // The prose half of the symbol page: scan headline, one price-action observation, and up to
  // three supporting bullets. Ported from scout's analytics/narrative.py.
  app.get("/api/symbol/:symbol/analysis", async (req) => {
    const { symbol } = req.params as { symbol: string };
    return buildSymbolAnalysis(config, market, symbol.toUpperCase());
  });

  // Builder-facing: events landing inside a chosen expiration that change a ticket's risk
  // character. An empty list means nothing was detected, which is itself a claim.
  app.get("/api/symbol/:symbol/warnings", async (req, reply) => {
    const { symbol } = req.params as { symbol: string };
    const { expiration } = req.query as { expiration?: string };
    if (!expiration || !/^\d{4}-\d{2}-\d{2}$/.test(expiration)) {
      return reply.code(400).send({ error: "expiration=YYYY-MM-DD is required" });
    }
    const sym = symbol.toUpperCase();
    return { symbol: sym, expiration, warnings: await buildEventWarnings(config, sym, expiration) };
  });

  app.get("/api/symbol/:symbol", async (req, reply) => {
    const { symbol } = req.params as { symbol: string };
    const sym = symbol.toUpperCase();
    const { bars, source } = await getDailyBars(config, market, sym);
    if (bars.length === 0) {
      return reply.code(404).send({
        error: "no candles for symbol (scout cache empty and DXLink backfill returned nothing)",
        available: candleSymbols(config),
      });
    }
    const closes = bars.map((b) => b.c);
    return {
      symbol: sym,
      bars,
      source,
      overlays: movingAverages(bars),
      levels: supportResistance(bars),
      trend: classifyTrend(closes),
    };
  });
}
