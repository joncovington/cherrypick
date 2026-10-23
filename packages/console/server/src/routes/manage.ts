import type { FastifyInstance } from "fastify";
import type { ManageLivePayload, ManagePayload } from "@console/shared";
import type { ConsoleConfig } from "../config.js";
import { readManage } from "../readers/manage.js";
import { readManageLive } from "../readers/manageLive.js";

export function registerManageRoutes(app: FastifyInstance, config: ConsoleConfig): void {
  app.get<{ Querystring: { session?: string } }>(
    "/api/manage",
    async (req): Promise<ManagePayload> => readManage(config, req.query.session),
  );
  // The newest evaluation re-marked from the stream cache: display only, polled by the Live tabs.
  app.get("/api/manage/live", async (): Promise<ManageLivePayload> => readManageLive(config));
}
