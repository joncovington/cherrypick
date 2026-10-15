import type { FastifyInstance } from "fastify";
import type { ManagePayload } from "@console/shared";
import type { ConsoleConfig } from "../config.js";
import { readManage } from "../readers/manage.js";

export function registerManageRoutes(app: FastifyInstance, config: ConsoleConfig): void {
  app.get<{ Querystring: { session?: string } }>(
    "/api/manage",
    async (req): Promise<ManagePayload> => readManage(config, req.query.session),
  );
}
