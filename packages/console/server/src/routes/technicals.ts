import type { FastifyInstance } from "fastify";
import type { TechnicalsChartPayload } from "@console/shared";
import type { ConsoleConfig } from "../config.js";
import { readChart } from "../readers/technicals.js";

export function registerTechnicalsRoutes(app: FastifyInstance, config: ConsoleConfig): void {
  app.get<{ Querystring: { symbol?: string } }>(
    "/api/technicals/chart",
    async (req): Promise<TechnicalsChartPayload> => readChart(config, req.query.symbol),
  );
}
