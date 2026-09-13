import type { FastifyInstance } from "fastify";
import type { AdvisorModulePayload, AdvisorPayload } from "@console/shared";
import type { ConsoleConfig } from "../config.js";
import { readAdvisor, readAdvisorModule } from "../readers/advisor.js";

export function registerAdvisorRoutes(app: FastifyInstance, config: ConsoleConfig): void {
  app.get<{ Querystring: { session?: string } }>(
    "/api/advisor",
    async (req): Promise<AdvisorPayload> => readAdvisor(config, req.query.session),
  );
  // One module's view: the experiment on it, its session strip, its queue, tomorrow's artifact.
  app.get<{ Params: { module: string } }>(
    "/api/advisor/module/:module",
    async (req): Promise<AdvisorModulePayload> => readAdvisorModule(config, req.params.module),
  );
}
