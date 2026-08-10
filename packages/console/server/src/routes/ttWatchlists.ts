import type { FastifyInstance } from "fastify";
import type { ConsoleConfig } from "../config.js";
import type { MarketDataService } from "../market/marketData.js";
import { setPublicPin } from "../store/consoleDb.js";
import {
  PUBLIC_ALLOWLIST,
  refreshTtWatchlists,
  resolveSource,
  symbolCard,
  ttLastError,
  ttWatchlistIndex,
  ttWatchlistPayload,
} from "../services/ttWatchlists.js";
import { warmCandles, WARM_MAX_SYMBOLS } from "../services/candleWarm.js";
import {
  chainSnapshotStatus,
  runChainSnapshot,
  snapshotOneSymbol,
  snapshotUniverse,
  SNAPSHOT_MAX_SYMBOLS,
} from "../services/chainEod.js";

const KEY_RE = /^(tt|public):.{1,80}$/;
const REFRESH_FLOOR_MS = 30_000;

let lastRefreshAt = 0;

export function registerTtWatchlistRoutes(
  app: FastifyInstance,
  config: ConsoleConfig,
  market: MarketDataService,
): void {
  app.get("/api/tt-watchlists", async () => {
    const index = await ttWatchlistIndex(config);
    return { ...index, lastError: ttLastError() };
  });

  app.get("/api/tt-watchlists/:key", async (req, reply) => {
    const { key } = req.params as { key: string };
    if (!KEY_RE.test(key)) return reply.code(400).send({ error: "invalid watchlist key" });
    const payload = await ttWatchlistPayload(config, key);
    if (payload === null) return reply.code(404).send({ error: "unknown watchlist" });
    return payload;
  });

  // Broker GETs — still POST behind the CSRF gate because it's a forced refetch.
  app.post("/api/tt-watchlists/refresh", async (req, reply) => {
    const now = Date.now();
    if (now - lastRefreshAt < REFRESH_FLOOR_MS) {
      return reply.code(429).send({
        error: `refreshed ${Math.round((now - lastRefreshAt) / 1000)}s ago — floor is ${REFRESH_FLOOR_MS / 1000}s`,
      });
    }
    lastRefreshAt = now;
    await refreshTtWatchlists(config, { force: true });
    return { ...(await ttWatchlistIndex(config)), lastError: ttLastError() };
  });

  app.post("/api/tt-watchlists/pins", async (req, reply) => {
    const body = (req.body ?? {}) as { name?: unknown; pinned?: unknown };
    const name = typeof body.name === "string" ? body.name : "";
    if (!PUBLIC_ALLOWLIST.includes(name)) {
      return reply.code(400).send({ error: "unknown public watchlist" });
    }
    setPublicPin(config, name, body.pinned === true);
    if (body.pinned === true) await refreshTtWatchlists(config, { force: true });
    return { ...(await ttWatchlistIndex(config)), lastError: ttLastError() };
  });

  // Broker/DXLink-heavy — button-triggered only, floored in the service.
  app.post("/api/candles/warm", async (req, reply) => {
    const body = (req.body ?? {}) as { source?: unknown };
    const source = typeof body.source === "string" ? body.source : "local";
    const symbols = resolveSource(config, source);
    if (symbols === null) return reply.code(404).send({ error: "unknown source" });
    return warmCandles(config, market, symbols.slice(0, WARM_MAX_SYMBOLS));
  });

  app.get("/api/chain-eod/status", async () => chainSnapshotStatus(config));

  const SYMBOL_RE = /^[A-Z][A-Z0-9./]{0,9}$/;

  // Single-symbol capture for the builder's on-selection auto-run: bounded
  // (one REST chain + one DXLink snapshot) with a per-symbol attempt floor.
  app.post("/api/chain-eod/symbol", async (req, reply) => {
    const body = (req.body ?? {}) as { symbol?: unknown };
    const sym = typeof body.symbol === "string" ? body.symbol.trim().toUpperCase() : "";
    if (!SYMBOL_RE.test(sym)) return reply.code(400).send({ error: "invalid symbol" });
    return snapshotOneSymbol(config, market, sym);
  });

  // Builder strategy surfaces — pure reads over the EOD chain snapshot.
  app.get("/api/builder/suggestions/:symbol", async (req, reply) => {
    const { symbol } = req.params as { symbol: string };
    const sym = symbol.toUpperCase();
    if (!SYMBOL_RE.test(sym)) return reply.code(400).send({ error: "invalid symbol" });
    const { sentiment } = req.query as { sentiment?: string };
    const { suggestions } = await import("../services/builderTemplates.js");
    return suggestions(config, sym, sentiment ?? "bullish");
  });

  app.get("/api/builder/income-grid/:symbol", async (req, reply) => {
    const { symbol } = req.params as { symbol: string };
    const sym = symbol.toUpperCase();
    if (!SYMBOL_RE.test(sym)) return reply.code(400).send({ error: "invalid symbol" });
    const { kind } = req.query as { kind?: string };
    const { incomeGrid } = await import("../services/builderTemplates.js");
    return incomeGrid(config, sym, kind === "call" ? "call" : "put");
  });

  app.get("/api/symbol-card/:symbol", async (req, reply) => {
    const { symbol } = req.params as { symbol: string };
    const sym = symbol.toUpperCase();
    if (!SYMBOL_RE.test(sym)) return reply.code(400).send({ error: "invalid symbol" });
    return symbolCard(config, market, sym);
  });

  // Broker/DXLink-heavy — button-triggered manual run of the daily snapshot,
  // floored in the service. `source` narrows to one list; default is the
  // whole universe the scheduler would cover.
  app.post("/api/chain-eod/run", async (req, reply) => {
    const body = (req.body ?? {}) as { source?: unknown };
    let symbols: string[];
    if (typeof body.source === "string" && body.source !== "all") {
      const resolved = resolveSource(config, body.source);
      if (resolved === null) return reply.code(404).send({ error: "unknown source" });
      symbols = resolved;
    } else {
      symbols = snapshotUniverse(config);
    }
    return runChainSnapshot(config, market, symbols.slice(0, SNAPSHOT_MAX_SYMBOLS));
  });
}
