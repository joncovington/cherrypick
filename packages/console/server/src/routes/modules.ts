import type { FastifyInstance } from "fastify";
import type { TradingMode } from "@console/shared";
import type { ConsoleConfig } from "../config.js";
import { readMeic } from "../readers/meic.js";
import { readFlies } from "../readers/flies.js";
import { readEarnings, readSymbolWatch } from "../readers/earnings.js";
import { readGex } from "../readers/gex.js";

function parseMode(q: unknown): TradingMode {
  const mode = (q as Record<string, unknown> | undefined)?.["mode"];
  return mode === "live" ? "live" : "paper";
}

export function registerModuleRoutes(app: FastifyInstance, config: ConsoleConfig): void {
  app.get("/api/meic", async (req) => readMeic(config, parseMode(req.query)));
  app.get("/api/flies", async (req) => readFlies(config, parseMode(req.query)));
  app.get("/api/earnings", async () => readEarnings(config));
  app.get("/api/earnings/upcoming", async () => readSymbolWatch(config));
  app.get("/api/gex", async () => readGex(config));
}
