import type { FastifyInstance } from "fastify";
import type { TechnicalsChartPayload, TechnicalsWatchlist } from "@console/shared";
import type { ConsoleConfig } from "../config.js";
import { readChart, readSetupsWatchlist } from "../readers/technicals.js";

export function registerTechnicalsRoutes(app: FastifyInstance, config: ConsoleConfig): void {
  app.get<{ Querystring: { symbol?: string } }>(
    "/api/technicals/chart",
    async (req): Promise<TechnicalsChartPayload> => readChart(config, req.query.symbol),
  );
  app.get("/api/technicals/setups", async (): Promise<TechnicalsWatchlist> => readSetupsWatchlist(config));
}
