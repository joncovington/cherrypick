import type { FastifyInstance } from "fastify";
import type { OverviewPayload, DeskBookPayload, DeskPayload } from "@console/shared";
import type { ConsoleConfig } from "../config.js";
import { readOverview } from "../readers/orchestrator.js";
import { readDesk, readDeskLive } from "../readers/desk.js";

export function registerOverviewRoutes(app: FastifyInstance, config: ConsoleConfig): void {
  app.get("/api/overview", async (): Promise<OverviewPayload> => readOverview(config));
  app.get("/api/desk", async (): Promise<DeskPayload> => readDesk(config));
  // The live book, for the Overview's cards to rotate to.
  app.get("/api/desk/live", async (): Promise<DeskBookPayload> => readDeskLive(config));
}
