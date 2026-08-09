import type { FastifyInstance } from "fastify";
import type { TradingMode } from "@console/shared";
import type { ConsoleConfig } from "../config.js";
import { readMeic, readMeicAnalytics, readMeicDeepAnalytics } from "../readers/meic.js";
import { readFlies, readFliesAnalytics, readFliesForest, readFliesMeta, type FliesFilter } from "../readers/flies.js";
import { readEarnings, readSymbolWatch, readEarningsAnalytics } from "../readers/earnings.js";
import { readGex } from "../readers/gex.js";
import { buildGexProfile, gexSymbols } from "../services/gexProfile.js";
import { buildSuiteReport } from "../services/report.js";
import { readLogTail } from "../readers/logs.js";

function parseMode(q: unknown): TradingMode {
  const mode = (q as Record<string, unknown> | undefined)?.["mode"];
  return mode === "live" ? "live" : "paper";
}

export function registerModuleRoutes(app: FastifyInstance, config: ConsoleConfig): void {
  app.get("/api/meic", async (req) => readMeic(config, parseMode(req.query)));
  app.get("/api/meic/analytics", async (req) => readMeicAnalytics(config, parseMode(req.query)));
  app.get("/api/meic/deep", async (req) => readMeicDeepAnalytics(config, parseMode(req.query)));
  const parseFliesFilter = (q: unknown): FliesFilter => {
    const query = (q ?? {}) as Record<string, unknown>;
    const date = typeof query["date"] === "string" && /^\d{4}-\d{2}-\d{2}$/.test(query["date"]) ? query["date"] : null;
    const arm = typeof query["arm"] === "string" && query["arm"] !== "" && query["arm"].length <= 40 ? query["arm"] : null;
    return { arm, date };
  };
  app.get("/api/flies", async (req) => readFlies(config, parseMode(req.query), parseFliesFilter(req.query)));
  app.get("/api/flies/analytics", async (req) =>
    readFliesAnalytics(config, parseMode(req.query), parseFliesFilter(req.query)),
  );
  app.get("/api/flies/meta", async (req) => readFliesMeta(config, parseMode(req.query)));
  app.get("/api/flies/forest", async (req) => {
    const f = parseFliesFilter(req.query);
    return readFliesForest(config, parseMode(req.query), f.date, f.arm);
  });
  app.get("/api/earnings", async () => readEarnings(config));
  app.get("/api/earnings/upcoming", async () => readSymbolWatch(config));
  app.get("/api/earnings/analytics", async (req) => readEarningsAnalytics(config, parseMode(req.query)));
  app.get("/api/gex", async () => readGex(config));
  app.get("/api/gex/symbols", async () => ({ symbols: gexSymbols(config) }));
  app.get("/api/gex/profile/:symbol", async (req) => {
    const { symbol } = req.params as { symbol: string };
    return buildGexProfile(config, symbol.toUpperCase());
  });
  app.get("/api/report", async () => buildSuiteReport(config));
  app.get("/api/logs", async () => ({ lines: readLogTail(config) }));
}
