import type { FastifyInstance } from "fastify";
import type { OptionsFlowPayload } from "@console/shared";
import type { ConsoleConfig } from "../config.js";
import { readOptionsFlow } from "../readers/optionsFlow.js";

export function registerOptionsFlowRoutes(app: FastifyInstance, config: ConsoleConfig): void {
  app.get<{ Querystring: { session?: string } }>(
    "/api/options-flow",
    async (req): Promise<OptionsFlowPayload> => readOptionsFlow(config, req.query.session),
  );
}
