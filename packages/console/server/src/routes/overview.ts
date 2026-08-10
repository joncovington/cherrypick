import type { FastifyInstance } from "fastify";
import type { OverviewPayload } from "@console/shared";
import type { ConsoleConfig } from "../config.js";
import { readOverview } from "../readers/orchestrator.js";

export function registerOverviewRoutes(app: FastifyInstance, config: ConsoleConfig): void {
  app.get("/api/overview", async (): Promise<OverviewPayload> => readOverview(config));
}
