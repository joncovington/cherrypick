import type { FastifyInstance } from "fastify";
import type { ManageBandsHistoryPayload, ManageLivePayload, ManagePayload } from "@console/shared";
import type { ConsoleConfig } from "../config.js";
import { readManage, readManageBandsHistory } from "../readers/manage.js";
import { readManageLive } from "../readers/manageLive.js";

export function registerManageRoutes(app: FastifyInstance, config: ConsoleConfig): void {
  app.get<{ Querystring: { session?: string } }>(
    "/api/manage",
    async (req): Promise<ManagePayload> => readManage(config, req.query.session),
  );
  // The newest evaluation re-marked from the stream cache: display only, polled by the Live tabs.
  app.get("/api/manage/live", async (): Promise<ManageLivePayload> => readManageLive(config));
  // Every stored evaluation's bands, for the bands-over-time card on Live -> portfolio.
  app.get("/api/manage/bands", async (): Promise<ManageBandsHistoryPayload> => readManageBandsHistory(config));
}
