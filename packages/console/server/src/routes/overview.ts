import type { FastifyInstance } from "fastify";
import type { OverviewPayload, DeskPayload } from "@console/shared";
import type { ConsoleConfig } from "../config.js";
import { readOverview } from "../readers/orchestrator.js";
import { readDesk } from "../readers/desk.js";

export function registerOverviewRoutes(app: FastifyInstance, config: ConsoleConfig): void {
  app.get("/api/overview", async (): Promise<OverviewPayload> => readOverview(config));
  app.get("/api/desk", async (): Promise<DeskPayload> => readDesk(config));
}
