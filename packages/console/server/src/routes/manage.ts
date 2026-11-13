import type { FastifyInstance } from "fastify";
import type { ManageBandsHistoryPayload, ManageLivePayload, ManageModelResult, ManagePayload } from "@console/shared";
import type { ConsoleConfig } from "../config.js";
import { readManageModel } from "../services/manageModelBridge.js";
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
  // Model a trade on a held name (manage's `model`, bridged): it prices a hypothesis, sends nothing.
  app.get<{ Querystring: { underlying?: string; leg?: string | string[]; account?: string } }>(
    "/api/manage/model",
    async (req): Promise<ManageModelResult> => {
      const legs = req.query.leg === undefined ? [] : Array.isArray(req.query.leg) ? req.query.leg : [req.query.leg];
      return readManageModel(req.query.underlying ?? "", legs, req.query.account ?? null);
    },
  );
}
