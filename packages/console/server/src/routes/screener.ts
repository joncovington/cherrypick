import type { FastifyInstance } from "fastify";
import type { ConsoleConfig } from "../config.js";
import type { MarketDataService } from "../market/marketData.js";
import { runScreener, type ScreenerParams } from "../services/screener.js";
import { resolveSource } from "../services/ttWatchlists.js";
import { addToBlacklist, listBlacklist, removeFromBlacklist } from "../store/consoleDb.js";

const SYMBOL_RE = /^[A-Z][A-Z0-9./]{0,9}$/;

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
  app.post("/api/screener/run", async (req, reply) => {
    const body = (req.body ?? {}) as Record<string, unknown>;
    const params: ScreenerParams = {
      dteMin: clamp(body["dteMin"], 25, 1, 365),
      dteMax: clamp(body["dteMax"], 45, 1, 365),
      wingWidthPct: clamp(body["wingWidthPct"], 0.05, 0.01, 0.3),
      minIvRank: clamp(body["minIvRank"], 0, 0, 100),
      minLiquidity: clamp(body["minLiquidity"], 0, 0, 4),
      maxSymbols: clamp(body["maxSymbols"], 60, 5, 100),
      quoteSource: body["quoteSource"] === "eod" ? "eod" : "live",
    };
    const source = typeof body["source"] === "string" ? body["source"] : "local";
    const symbols = resolveSource(config, source);
    if (symbols === null) return reply.code(404).send({ error: "unknown source" });
    return runScreener(config, market, symbols, params, source);
  });

  // Learned + manual symbol blacklist ("no weekly options" etc.), user-clearable.
  app.get("/api/blacklist", async () => ({ rows: listBlacklist(config) }));

  app.post("/api/blacklist", async (req, reply) => {
    const body = (req.body ?? {}) as { symbol?: unknown; reason?: unknown };
    const symbol = typeof body.symbol === "string" ? body.symbol.trim().toUpperCase() : "";
    if (!SYMBOL_RE.test(symbol)) return reply.code(400).send({ error: "invalid symbol" });
    const reason = typeof body.reason === "string" && body.reason.trim() !== "" ? body.reason.trim() : "manual";
    addToBlacklist(config, symbol, reason);
    return { rows: listBlacklist(config) };
  });

  app.delete("/api/blacklist/:symbol", async (req) => {
    const { symbol } = req.params as { symbol: string };
    removeFromBlacklist(config, symbol.toUpperCase());
    return { rows: listBlacklist(config) };
  });
}
