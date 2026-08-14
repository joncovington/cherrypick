import type { FastifyInstance } from "fastify";
import type { AdvisorPayload } from "@console/shared";
import type { ConsoleConfig } from "../config.js";
import { readAdvisor } from "../readers/advisor.js";

export function registerAdvisorRoutes(app: FastifyInstance, config: ConsoleConfig): void {
  app.get<{ Querystring: { session?: string } }>(
    "/api/advisor",
    async (req): Promise<AdvisorPayload> => readAdvisor(config, req.query.session),
  );
}
