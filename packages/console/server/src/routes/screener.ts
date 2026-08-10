import type { FastifyInstance } from "fastify";
import type { ConsoleConfig } from "../config.js";
import type { MarketDataService } from "../market/marketData.js";
import { getWatchlist } from "../store/consoleDb.js";
import { runScreener, type ScreenerParams } from "../services/screener.js";

function clamp(v: unknown, def: number, lo: number, hi: number): number {
  const n = Number(v);
  return Number.isFinite(n) ? Math.min(hi, Math.max(lo, n)) : def;
}

export function registerScreenerRoutes(
  app: FastifyInstance,
  config: ConsoleConfig,
  market: MarketDataService,
): void {
  // Broker-heavy — POST behind the CSRF gate, button-triggered only, floored in the service.
  app.post("/api/screener/run", async (req) => {
    const body = (req.body ?? {}) as Record<string, unknown>;
    const params: ScreenerParams = {
      dteMin: clamp(body["dteMin"], 25, 1, 365),
      dteMax: clamp(body["dteMax"], 45, 1, 365),
      wingWidthPct: clamp(body["wingWidthPct"], 0.05, 0.01, 0.3),
      minIvRank: clamp(body["minIvRank"], 0, 0, 100),
      minLiquidity: clamp(body["minLiquidity"], 0, 0, 4),
    };
    return runScreener(config, market, getWatchlist(config), params);
  });
}
